"""Detect English labels inside figures and paint their translations on matching output artwork."""
from collections import defaultdict
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile

import cv2
import numpy as np
import pymupdf
from PIL import ImageFont

from upstream_runtime import cache_root
from scanned import prepare_english_ocr
from output_documents import open_outputs, replace_outputs, right_half, translated_page


def _caption_boxes(page):
    captions = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines", []):
            label = "".join(span["text"] for span in line["spans"]).strip()
            if re.match(r"^(?:fig(?:ure)?\.?|scheme)\s*\d+\s*[.:(–-]", label, re.I):
                captions.append(pymupdf.Rect(line["bbox"]))
    return captions


def _regions(page, load_layout):
    bounds = page.rect
    images = [pymupdf.Rect(image["bbox"]) for image in page.get_image_info()]
    candidates = [box & bounds for box in images if box.width >= 55 and box.height >= 28
                  and box.get_area() >= bounds.get_area() * .008]
    captions = _caption_boxes(page)
    found = []
    for box in candidates:
        near = any(c.x1 > box.x0 and c.x0 < box.x1 and
                   (-2 <= c.y0-box.y1 <= 27 or -2 <= box.y0-c.y1 <= 21) for c in captions)
        if near and box.get_area() < bounds.get_area()*.82:
            found.append(box)
    # A publication can include vectors, a full-page scan, or an uncaptioned
    # illustration. Use the same local document model as the native pipeline.
    if not captions and not candidates:
        return []
    if not found or len(candidates) > len(found) or len(captions) > len(found):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.45,1.45),alpha=False,annots=False)
        pixels = np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        result = load_layout().predict(pixels)[0]
        for obj in result.boxes:
            if result.names[int(obj.cls)] != "figure" or float(obj.conf) < .46:
                continue
            x0,y0,x1,y1 = map(float,obj.xyxy)
            box = pymupdf.Rect(x0*bounds.width/pix.width,y0*bounds.height/pix.height,
                               x1*bounds.width/pix.width,y1*bounds.height/pix.height) & bounds
            for caption in captions:
                if caption.x0 < box.x1 and caption.x1 > box.x0 and box.y0 < caption.y0 < box.y1:
                    box.y1 = caption.y0
            if box.height>=24 and box.width>=50 and not any(
                (r & box).get_area() > .64*min(r.get_area(),box.get_area()) for r in found):
                found.append(box)
    unique=[]
    for box in sorted(found,key=lambda b:(b.y0,b.x0)):
        if not any((r & box).get_area() > .85*min(r.get_area(),box.get_area()) for r in unique):
            unique.append(box)
    return unique


def _text_rows(words):
    lines = defaultdict(list)
    for word in words:
        lines[(word[5],word[6])].append(word)
    for line in lines.values():
        group = []
        for word in sorted(line,key=lambda w:w[0]):
            if re.search(r"[{}|<>]",word[4]):
                if group:
                    yield group
                    group=[]
                continue
            if group and word[0]-group[-1][2] > 2.6*max(word[3]-word[1],group[-1][3]-group[-1][1]):
                yield group
                group=[]
            group.append(word)
        if group:
            yield group


def _english(words):
    # Number-of-patients suffixes and isolated graph ornament belong to the
    # picture. Keep their pixels; only translate the neighbouring English.
    while words and not re.search(r"[A-Za-z0-9]",words[0][4]):
        words=words[1:]
    for i,w in enumerate(words):
        if re.match(r"^[({]?n\s*=",w[4],re.I):
            words=words[:i]
            break
    if not words:
        return
    while words and not re.search(r"[A-Za-z0-9]",words[-1][4]):
        words=words[:-1]
    if not words:
        return
    source=" ".join(w[4] for w in words).strip(' "“”—·•«»')
    while source.endswith(")") and source.count(")")>source.count("("):
        source=source[:-1].strip()
    if not re.search(r"[A-Za-z]{3}",source) and source.lower() not in ("no","yes"):
        return
    if re.match(r"^(?:fig(?:ure)?|table)\.?\s*\d+",source,re.I):
        return
    if re.search(r"[{}∑∫√<>^_]",source) or source.endswith("+") or len(source)>230 or len(source.split())>28:
        return
    if re.search(r"\b(?:sin|cos|tan|log|ln|lim)\s*[({]|[+*/]\s*[A-Za-z]\s*[=+*/]",source):
        return
    if re.fullmatch(r"[A-Z\d.-]{2,12}",source):
        return
    return source,words


def _ocr_words(gray, dpi, tessdir):
    rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    pix = pymupdf.Pixmap(pymupdf.csRGB, rgb.shape[1], rgb.shape[0], rgb.tobytes(), False)
    pix.set_dpi(dpi, dpi)
    data = pix.pdfocr_tobytes(language="eng", tessdata=str(tessdir))
    with pymupdf.open(stream=data, filetype="pdf") as ocr:
        return ocr[0].get_text("words")


def _boxed_answers(gray, words, pix, scale, dpi, tessdir, check):
    """Re-read isolated short branch labels without their frames or connecting lines."""
    if not any(w[4].lower() in ("yes", "no") for w in words):
        return
    contours, tree = cv2.findContours(np.uint8(gray < 185), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if tree is None:
        return
    line_id = 1 + max(w[5] for w in words)
    for i, contour in enumerate(contours):
        x, y, w, h = cv2.boundingRect(contour)
        # Only a small, enclosed, mostly rectangular white interior.
        if (tree[0,i,3] < 0 or not 2.5*scale < h < 11*scale or not 1.35 < w/h < 4
                or cv2.contourArea(contour) < .78*w*h):
            continue
        check()
        box = pymupdf.Rect((pix.x+x)/scale, (pix.y+y)/scale, (pix.x+x+w)/scale, (pix.y+y+h)/scale)
        if any(re.fullmatch(r"[A-Za-z0-9.]+", token[4]) and
               box.contains(pymupdf.Point((token[0]+token[2])/2, (token[1]+token[3])/2)) for token in words):
            continue
        inset = max(2, round(scale*.75))
        inner = gray[y+inset:y+h-inset, x+inset:x+w-inset]
        if not inner.size or np.median(inner) < 185:
            continue
        enlarged = cv2.resize(inner, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        sample = cv2.copyMakeBorder(enlarged,22,22,20,20,cv2.BORDER_CONSTANT,value=255)
        guess = _ocr_words(sample, dpi, tessdir)
        if len(guess) != 1 or guess[0][4].lower() not in ("yes", "no"):
            continue
        token = guess[0][4]
        ink = _short_answer_ink(inner, token)
        if ink is None:
            continue
        bx, by, bw, bh = cv2.boundingRect(ink)
        left, top = pix.x+x+inset+bx, pix.y+y+inset+by
        yield (left/scale, top/scale, (left+bw)/scale, (top+bh)/scale, token, line_id, 0, 0)
        line_id += 1


def _ocr(region,page,tessdir,check=lambda:None):
    # Remove strong chart rules from the recognition image only.
    scale=min(4, (5_000_000/region.get_area())**.5)
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=region,alpha=False,annots=False)
    if not pix.width or not pix.height:
        return []
    rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    black=(gray<135).astype(np.uint8)*255
    horizontal=cv2.morphologyEx(black,cv2.MORPH_OPEN,np.ones((1,max(45,round(scale*14))),np.uint8))
    vertical=cv2.morphologyEx(black,cv2.MORPH_OPEN,np.ones((max(50,round(scale*16)),1),np.uint8))
    clean=255-(black & ~cv2.dilate(horizontal|vertical,np.ones((2,2),np.uint8)))
    pad=12
    clean=cv2.copyMakeBorder(clean,pad,pad,pad,pad,cv2.BORDER_CONSTANT,value=255)
    dpi=round(scale*72)
    words=_ocr_words(clean,dpi,tessdir)
    converted=[]
    for w in words:
        pts=[((v*dpi/72)-pad+(pix.x if i%2==0 else pix.y))/scale for i,v in enumerate(w[:4])]
        converted.append((*pts,*w[4:]))
    return converted + list(_boxed_answers(gray, converted, pix, scale, dpi, tessdir, check))


def _context(document,page,region):
    near=[]
    before=[]
    for block in page.get_text("blocks"):
        if len(block)<7 or block[6]!=0:
            continue
        box=pymupdf.Rect(block[:4])
        text=" ".join(block[4].split())
        if not text or len(text)<12 or box.x1<region.x0 or box.x0>region.x1:
            continue
        if 0<=region.y0-box.y1<65:
            before.append((box.y1,text))
        elif -2<=box.y0-region.y1<32:
            near.append((box.y0,text))
    caption=" ".join(t for _,t in sorted(near))[:650]
    above=" ".join(t for _,t in sorted(before))[-680:]
    prev=""
    if page.number and len(above)<590:
        previous=document[page.number-1]
        prev=" ".join(previous.get_text(clip=previous.rect,sort=True).split())[-450:]
    return " ".join(x for x in (prev,above,caption) if x)[-1600:]


def extract(source,check,status,*,on_progress=None):
    labels=[]
    layout=None
    tessdir=None

    def load_layout():
        nonlocal layout
        if layout is None:
            from babeldoc.docvision.doclayout import OnnxModel
            path = Path(os.environ.get("RTRANSLATE_LAYOUT_MODEL",
                                       cache_root()/"babeldoc"/"models"/"doclayout_yolo_docstructbench_imgsz1024.onnx"))
            layout = OnnxModel(str(path)) if path.is_file() else OnnxModel.from_pretrained()
        return layout

    with pymupdf.open(source) as pdf:
        for page in pdf:
            check()
            if page.rotation:
                if on_progress:
                    on_progress(page.number+1,len(pdf))
                continue
            try:
                regions=_regions(page,load_layout)
            except InterruptedError:
                raise
            except (ValueError, RuntimeError, OSError):
                status(f"第 {page.number+1} 页的图像区域无法判定，已保留原图。")
                if on_progress:
                    on_progress(page.number+1,len(pdf))
                continue
            for index,region in enumerate(regions,1):
                check()
                status(f"正在读取第 {page.number+1} 页图内文字…")
                native=[w for w in page.get_text("words",clip=region)
                        if region.contains(pymupdf.Rect(*w[:4]))]
                # Rendered crops also cover outlined letters, masked text and
                # mixed vector/bitmap figures. Use native spans for vector art.
                raster=any((pymupdf.Rect(im["bbox"]) & region).get_area()>.40*region.get_area()
                           for im in page.get_image_info())
                if raster or not native:
                    try:
                        if tessdir is None:
                            tessdir=prepare_english_ocr(check,status)
                        words=_ocr(region,page,tessdir,check)
                    except InterruptedError:
                        raise
                    except (ValueError,RuntimeError,OSError,cv2.error):
                        status(f"第 {page.number+1} 页图 {index} 的文字无法识别，已保留原图。")
                        continue
                else:
                    blocks=page.get_text("dict",clip=region,flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
                    tilted=[pymupdf.Rect(line["bbox"]) for block in blocks for line in block.get("lines",[])
                            if abs(line["dir"][1])>.06 or line["dir"][0]<.99]
                    words=[word for word in native if not any(
                        rect.contains(pymupdf.Point((word[0]+word[2])/2,(word[1]+word[3])/2)) for rect in tilted)]
                first=len(labels)
                for line in _text_rows(words):
                    item=_english(line)
                    if not item:
                        continue
                    text,group=item
                    boxes=[pymupdf.Rect(*word[:4]) for word in group]
                    rect=pymupdf.Rect(boxes[0])
                    for box in boxes[1:]:
                        rect|=box
                    if rect.width<4 or rect.height<2.5 or not region.contains(rect):
                        continue
                    labels.append({"page":page.number+1,"figure":index,"rect":list(rect),
                                   "source":text,"words":[list(box) for box in boxes],"raster":raster})
                if len(labels)>first:
                    labels[first]["context"]=_context(pdf,page,region)
            if on_progress:
                on_progress(page.number+1,len(pdf))
    return labels


def _page_rgb(page, scale):
    pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False,annots=False)
    return np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)


def _short_answer_ink(gray,source):
    """Select a level row of letter components, leaving neighbouring node counts intact."""
    if source.lower() not in ("yes","no"):
        return None
    count=len(source)
    for level in (85,110,135):
        _,labels,stats,_=cv2.connectedComponentsWithStats(np.uint8(gray<level))
        candidates=sorted((i for i,(_,y,w,h,area) in enumerate(stats) if i and w>=3 and h>=5
                           and area>=10 and y+h<gray.shape[0]-1 and h/5<=w<=1.5*h),
                          key=lambda i:stats[i,0])
        matches=[]
        for first in candidates:
            baseline=stats[first,1]+stats[first,3]
            height=stats[first,3]
            group=[first]
            end=stats[first,0]+stats[first,2]
            for i in candidates:
                x,y,w,h,_=stats[i]
                if end-1<=x<=end+.6*height and abs(y+h-baseline)<=max(2,height*.22) and .52*height<=h<=1.3*height:
                    group.append(i)
                    end=x+w
                    if len(group)==count:
                        mask=np.uint8(np.isin(labels,group))*255
                        matches.append((sum(stats[j,4] for j in group),mask))
                        break
        if matches:
            return max(matches,key=lambda item:item[0])[1]
    return None


def _patch(row,original,translated,scale):
    """Keep numbers, freestanding rules and nonuniform backgrounds intact."""
    rect=pymupdf.Rect(row["rect"])
    h,w=original.shape[:2]
    x0=max(0,int(rect.x0*scale)-3)
    y0=max(0,int(rect.y0*scale)-3)
    x1=min(w,int(np.ceil(rect.x1*scale))+3)
    y1=min(h,int(np.ceil(rect.y1*scale))+3)
    crop=original[y0:y1,x0:x1]
    target=translated[y0:y1,x0:x1]
    if crop.size==0 or crop.shape!=target.shape:
        return None
    difference=np.max(np.abs(crop.astype('int16')-target.astype('int16')),axis=2)
    if np.mean(difference>22)>.014:
        # The native renderer has translated or moved this part of the figure.
        return None
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
    threshold=max(45,min(150,int(np.percentile(gray,92))-50))
    ink=np.uint8(gray<threshold)*255
    # OCR PDF word boxes inherit the height of their complete line. On
    # flowcharts one isolated arrow or bracket can make that height three
    # times the lettering. Infer the actual baseline from the visible ink.
    binary=cv2.copyMakeBorder(ink,3,3,3,3,cv2.BORDER_CONSTANT,value=0)
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(21,round(.75*ink.shape[1]))),np.uint8))
    vertical=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((max(27,round(.85*ink.shape[0])),1),np.uint8))
    rules=(horizontal|vertical)[3:-3,3:-3]
    allowed=np.zeros(ink.shape,np.uint8)
    for word in row["words"]:
        l=max(0,int(word[0]*scale)-x0-1)
        t=max(0,int(word[1]*scale)-y0-2)
        r=min(ink.shape[1],int(np.ceil(word[2]*scale))-x0+2)
        b=min(ink.shape[0],int(np.ceil(word[3]*scale))-y0+2)
        allowed[t:b,l:r]=255
    glyph=ink & allowed & ~cv2.dilate(rules,np.ones((3,3),np.uint8))
    n, lab, stats, centers=cv2.connectedComponentsWithStats(glyph)
    components=[i for i in range(1,n) if stats[i,cv2.CC_STAT_AREA]>=2 and
                stats[i,cv2.CC_STAT_HEIGHT]>=2]
    if not components:
        return None
    mid_y=np.median([centers[i,1] for i in components])
    glyph_height=np.median([stats[i,cv2.CC_STAT_HEIGHT] for i in components])
    components=[i for i in components if abs(centers[i,1]-mid_y)<=max(4,.75*glyph_height)]
    glyph=np.uint8(np.isin(lab,components))*255
    answer=_short_answer_ink(gray,row["source"])
    if answer is not None:
        glyph=answer
    points=cv2.findNonZero(glyph)
    if points is None:
        return None
    x,y,width,height=cv2.boundingRect(points)
    if height<6 or width<4 or (row["source"].lower() in ("yes","no") and answer is None and height>8*scale):
        return None
    tx,ty=max(x-2,0),max(y-2,0)
    end_x,end_y=min(x+width+2,crop.shape[1]),min(y+height+2,crop.shape[0])
    paper=crop[ty:end_y,tx:end_x]
    ring=(cv2.dilate(glyph,np.ones((7,7),np.uint8))>0) & (cv2.dilate(glyph,np.ones((3,3),np.uint8))==0)
    surround=crop[ring]
    if len(surround)<4:
        return None
    color=np.median(surround,axis=0)
    if np.mean(np.max(np.abs(surround-color),axis=1)<27)<.67 or np.mean(color)<(73 if answer is not None else 130):
        return None
    # Paint an opaque high-resolution patch. A transparent mask leaves
    # antialiased ghosts of the original letters in some PDF renderers.
    clean=paper.copy()
    if answer is not None:
        clean[:]=color
    else:
        clean[rules[ty:end_y,tx:end_x]==0]=color
    ok,png=cv2.imencode(".png",cv2.cvtColor(clean,cv2.COLOR_RGB2BGR))
    if not ok:
        return None
    box=pymupdf.Rect((x0+tx)/scale,(y0+ty)/scale,(x0+end_x)/scale,(y0+end_y)/scale)
    ink_box=pymupdf.Rect((x0+x)/scale,(y0+y)/scale,(x0+x+width)/scale,(y0+y+height)/scale)
    return box,png.tobytes(),color,ink_box


def _paint(page,row,font,metrics,original,translated,scale):
    target=row.get("translation","").strip()
    if not re.search(r"[\u3400-\u9fff]",target) or "\n" in target:
        return False
    patch=_patch(row,original,translated,scale)
    if patch is None:
        return False
    box,image,bg,rect=patch
    # Enforce the source rectangle; avoid squeezing an overlong explanation
    # through a connector or a neighbouring numerical value.
    _,top,_,bottom=metrics.getbbox(target,anchor="ls")
    ascent,descent=-top/256,-bottom/256
    line_height=max(.5,ascent-descent)
    size=min(rect.height/line_height,rect.width/max(font.text_length(target,fontsize=1),.01))
    width_factor=1.
    if size < .61*rect.height:
        size=.61*rect.height
        width_factor=min(1.,rect.width/font.text_length(target,fontsize=size))
    if size<2.1 or width_factor<.65:
        return False
    page.insert_image(box,stream=image,keep_proportion=False)
    line_width=font.text_length(target,fontsize=size)*width_factor
    origin=pymupdf.Point(rect.x0+(rect.width-line_width)/2,
                         rect.y0+(rect.height-size*line_height)/2+size*ascent)
    color=(.08,.12,.14) if np.mean(bg)>125 else (.96,.96,.94)
    page.insert_text(origin,target,fontsize=size,fontname="FigureCJK",color=color,
                     morph=(origin,pymupdf.Matrix(width_factor,1)))
    return True


def render(source,mono,dual,labels):
    """Atomically prepare both edited documents; report visible translated labels."""
    rows=defaultdict(list)
    for item in labels:
        if item.get("translation"):
            rows[item["page"]-1].append(item)
    if not rows:
        return 0
    source=Path(source)
    font=pymupdf.Font("cjk")
    metrics=ImageFont.truetype(BytesIO(font.buffer),size=256)
    done=0
    with tempfile.TemporaryDirectory(dir=Path(mono or dual).parent,prefix=".figure-labels-") as work:
        work=Path(work)
        with pymupdf.open(source) as original, open_outputs(mono,dual) as (one,two), pymupdf.open() as layers:
            if any(doc is not None and len(doc)!=len(original) for doc in (one,two)):
                raise ValueError("图文标注的输入与译文页数不一致。")
            mapped=[]
            for index,entries in sorted(rows.items()):
                p=original[index]
                w,h=p.rect.width,p.rect.height
                if p.rotation:
                    continue
                if two is not None:
                    d=two[index]
                    if d.rotation or abs(d.rect.width-2*w)>1 or abs(d.rect.height-h)>1:
                        continue
                with translated_page(one,two,index) as m:
                    if m.rotation or abs(m.rect.width-w)>1 or abs(m.rect.height-h)>1:
                        continue
                    scale=min(4.,(9_000_000/(w*h))**.5)
                    a=_page_rgb(p,scale)
                    b=_page_rgb(m,scale)
                layer=layers.new_page(width=w,height=h)
                layer.insert_font(fontname="FigureCJK",fontbuffer=font.buffer)
                applied=sum(_paint(layer,row,font,metrics,a,b,scale) for row in entries)
                if applied:
                    mapped.append((index,layer.number))
                    done+=applied
            if not mapped:
                return 0
            layers.subset_fonts()
            for index,layer_index in mapped:
                if one is not None:
                    one[index].show_pdf_page(one[index].rect,layers,layer_index)
                if two is not None:
                    two[index].show_pdf_page(right_half(two[index]),layers,layer_index)
            if one is not None:
                one.save(work/"mono.pdf",garbage=3,deflate=True)
            if two is not None:
                two.save(work/"dual.pdf",garbage=3,deflate=True)
        replace_outputs(work,mono,dual)
    return done
