"""Prepare image-only English pages for the ordinary PDFMathTranslate-next pipeline."""
from collections import defaultdict
import os
from pathlib import Path
import re
import tempfile
import unicodedata

import pymupdf
from upstream_runtime import cache_root
from upstream_runtime.bootstrap import Asset, _obtain

_ENGLISH = Asset(
    "eng.traineddata",
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata",
    "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    4113088,
)


def prepare_english_ocr(check, status):
    """Prepare the pinned English tessdata; return its directory."""
    offline = os.environ.get("RTRANSLATE_OFFLINE_ASSETS")
    folders = [Path(offline)] if offline else []
    return _obtain(_ENGLISH, folders, cache_root().parent/"OCR", check, status).parent


def _looks_scanned(page):
    # Occupancy on a small grid handles tiled scans and avoids double-counting
    # image masks or a repeated image on an otherwise born-digital page.
    import numpy as np
    rect = page.rect
    if rect.is_empty:
        return False
    occupied = np.zeros((40,40), dtype=bool)
    rotation = page.rotation_matrix
    for obj in page.get_image_info():
        box = pymupdf.Rect(obj["bbox"]) * rotation & rect
        if box.is_empty:
            continue
        x0 = max(0, round((box.x0 - rect.x0) * 40 / rect.width))
        x1 = min(40, round((box.x1 - rect.x0) * 40 / rect.width))
        y0 = max(0, round((box.y0 - rect.y0) * 40 / rect.height))
        y1 = min(40, round((box.y1 - rect.y0) * 40 / rect.height))
        occupied[y0:y1,x0:x1] = True
    if occupied.mean() < .58:
        return False
    glyphs = 0
    for span in page.get_texttrace():
        if span["type"] in (0,1) and span["opacity"] > .12:
            glyphs += sum(1 for c in span["chars"] if 0 <= c[0] <= 0x10FFFF and chr(c[0]).isalpha())
            if glyphs >= 70:
                return False
    return True


def _prose_around_formulas(line):
    """Keep nearby operators, variables, quantities, and superscripts painted."""
    protected = set()
    for i,word in enumerate(line):
        if not re.search(r"[=∫∑√<>≤≥±×÷_^]", word[4]):
            continue
        protected.add(i)
        for step in (-1,1):
            j = i+step
            while 0<=j<len(line):
                token = line[j][4].strip("()[]{}:,;.")
                if not token or not (any(c.isdigit() for c in token) or
                                     len(token)<=1 or token.isupper() and len(token)<=4):
                    break
                protected.add(j)
                j += step
    run=[]
    for i,word in enumerate(line):
        if i not in protected:
            run.append(word)
        elif run:
            yield run
            run=[]
    if run:
        yield run


def _text_runs(words):
    """Keep recognizable English prose. Leave equation/number-only areas in the image."""
    by_line = defaultdict(list)
    for word in words:
        by_line[(word[5], word[6])].append(word)
    for line in by_line.values():
        groups = [[]]
        for w in sorted(line, key=lambda item: item[0]):
            previous = groups[-1][-1] if groups[-1] else None
            if previous and w[0]-previous[2] > 2.5 * max(w[3]-w[1], previous[3]-previous[1]):
                groups.append([])
            groups[-1].append(w)
        for group in (part for words_in_column in groups for part in _prose_around_formulas(words_in_column)):
            text = unicodedata.normalize("NFKC", " ".join(w[4] for w in group)).strip()
            letters = re.findall(r"[A-Za-z]{2,}", text)
            if not letters or sum(map(len, letters)) < 4:
                continue
            if len(letters) < 4 and re.search(r"[=∫∑√<>×÷_^]",text):
                continue
            text = text.translate(str.maketrans({"–":"-","—":"-","’":"'","“":'"',"”":'"',"−":"-"}))
            if any(ord(c)>255 for c in text):
                continue
            yield group, text


def _protected_layout(model, pixels):
    result = model.predict(pixels)[0]
    boxes = [(result.names[int(obj.cls)],pymupdf.Rect(*map(float,obj.xyxy)))
             for obj in result.boxes if float(obj.conf)>=.30]
    protected=[]
    for kind,box in boxes:
        if kind not in {"figure","table","isolate_formula"}:
            continue
        area=pymupdf.Rect(box)
        if kind=="figure":
            # A CONSORT chart, for example, may have its upper nodes detected
            # as small centred text boxes. Include the connected label stack.
            short=[r for label,r in boxes if label in {"plain text","title"}
                   and r.width<box.width*.72 and r.height<box.height*.13
                   and r.x0>box.x0 and r.x1<box.x1
                   and abs((r.x0+r.x1)-(box.x0+box.x1)) < box.width*.46]
            for label_box in sorted(short,key=lambda r:r.y1,reverse=True):
                if 0 <= area.y0-label_box.y1 <= box.height*.10:
                    area |= label_box
            area = pymupdf.Rect(area.x0-box.width*.015,area.y0-box.height*.035,
                                area.x1+box.width*.015,area.y1+box.height*.012)
        protected.append(area)
    return protected


def _clean_scan(page, tessdir, layout, check):
    import cv2
    import numpy as np

    rect = page.rect
    dpi = int(min(220,72*(9_000_000/(rect.width*rect.height))**.5))
    if dpi < 35:
        raise ValueError(f"第 {page.number+1} 页超出本机 OCR 支持的页面尺寸。")
    image = page.get_pixmap(dpi=dpi,colorspace=pymupdf.csRGB,alpha=False,annots=False)
    # MuPDF wheels embed Tesseract and Leptonica. Supplying traineddata
    # explicitly makes the OCR independent of PATH and global installations.
    data = image.pdfocr_tobytes(language="eng",tessdata=str(tessdir),compress=True)
    check()
    with pymupdf.open(stream=data,filetype="pdf") as ocr:
        words = ocr[0].get_text("words")
        detected = list(_text_runs(words))
        if not detected:
            return None
        width, height = ocr[0].rect.width, ocr[0].rect.height
    rgb = np.frombuffer(image.samples,dtype=np.uint8).reshape(image.height,image.width,3)
    gray = cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    px, py = image.width/width, image.height/height
    protected = _protected_layout(layout,rgb)
    mask = np.zeros(gray.shape, dtype=np.uint8)
    white_paper = np.zeros(gray.shape, dtype=bool)
    runs = []
    for group, text in detected:
        check()
        word_boxes = [pymupdf.Rect(*w[:4]) for w in group]
        box = pymupdf.Rect(word_boxes[0])
        for b in word_boxes[1:]:
            box |= b
        if box.is_empty:
            continue
        l,t,r,b = max(0,int(box.x0*px)-3), max(0,int(box.y0*py)-3), min(image.width,int(box.x1*px)+4), min(image.height,int(box.y1*py)+4)
        run_bounds = pymupdf.Rect(l,t,r,b)
        if r<=l or b<=t or any((run_bounds & region).get_area() > .10*run_bounds.get_area()
                                for region in protected):
            continue
        white = float(np.percentile(gray[t:b,l:r],90))
        if white<145:
            continue
        for word in word_boxes:
            x0,y0 = max(0,int(word.x0*px)-2),max(0,int(word.y0*py)-2)
            x1,y1 = min(image.width,int(word.x1*px)+3),min(image.height,int(word.y1*py)+3)
            region = gray[y0:y1,x0:x1]
            if region.size:
                if white >= 252 and np.mean(region>=248) >= .40:
                    white_paper[y0:y1,x0:x1] = True
                mask[y0:y1,x0:x1] |= ((region < white-18)*255).astype(np.uint8)
        target = pymupdf.Rect(box.x0*rect.width/width,box.y0*rect.height/height,
                              box.x1*rect.width/width,box.y1*rect.height/height)
        runs.append((target,text))
    if not runs:
        return None
    # Keep long table rules and illustration strokes crossing a recognized word.
    ink = np.where(gray<165,255,0).astype(np.uint8)
    rules_h = cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,max(48,int(dpi*.31))),np.uint8))
    rules_v = cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(48,int(dpi*.31)),1),np.uint8))
    mask = cv2.dilate(mask,np.ones((2,2),np.uint8))
    mask[(rules_h|rules_v)>0] = 0
    clean = cv2.inpaint(rgb,mask,3,cv2.INPAINT_TELEA)
    # On clearly white paper remove pale antialiasing fringes as well. Retain
    # coloured paper texture and long genuine rules via the inpainted image.
    clean[white_paper & ((rules_h | rules_v)==0)] = 255
    for protected_box in protected:
        x0,y0,x1,y1 = (protected_box & pymupdf.Rect(0,0,image.width,image.height)).irect
        if x1>x0 and y1>y0:
            clean[y0:y1,x0:x1]=rgb[y0:y1,x0:x1]
    success, png = cv2.imencode(".png",cv2.cvtColor(clean,cv2.COLOR_RGB2BGR))
    if not success:
        raise ValueError("无法生成 OCR 工作页。")
    return png.tobytes(), runs


def prepare_scanned(source, dest, progress, cancel):
    """Return (PDF to translate, one-based OCR page numbers). The source stays intact."""
    source, dest = Path(source),Path(dest)
    if source.resolve()==dest.resolve():
        raise ValueError("OCR 工作副本与原文件必须使用不同路径。")
    def check():
        if cancel.is_set():
            raise InterruptedError("OCR 已取消。")

    with pymupdf.open(source) as doc:
        candidates=[]
        for page in doc:
            check()
            if _looks_scanned(page):
                candidates.append(page.number)
        if not candidates:
            if not any(page.get_text().strip() for page in doc):
                raise ValueError("此 PDF 没有可翻译的正文或可识别的英文扫描页。")
            return source,[]
        progress("正在准备英文扫描识别（首次约 4.1 MB）",0.0)
        tessdir = prepare_english_ocr(check,lambda msg:progress(msg,0.0))
        from babeldoc.docvision.doclayout import OnnxModel
        fallback = cache_root()/"babeldoc"/"models"/"doclayout_yolo_docstructbench_imgsz1024.onnx"
        model_path = Path(os.environ.get("RTRANSLATE_LAYOUT_MODEL",str(fallback)))
        layout = OnnxModel(str(model_path)) if model_path.is_file() else OnnxModel.from_pretrained()
        processed=[]
        for index, page_no in enumerate(candidates):
            check()
            progress(f"正在识别扫描页 {page_no+1} / {len(doc)}",100*index/len(candidates))
            page=doc[page_no]
            prepared = _clean_scan(page,tessdir,layout,check)
            if prepared is None:
                continue
            png,runs=prepared
            # Reset only the scanned page's painted objects in this in-memory copy.
            # Existing native pages retain their original streams, images and fonts.
            display = pymupdf.Rect(page.rect)
            page.remove_rotation()
            page.set_mediabox(pymupdf.Rect(0,0,display.width,display.height))
            stream=doc.get_new_xref()
            doc.update_object(stream,"<<>>")
            doc.update_stream(stream,b"")
            page.set_contents(stream)
            page.insert_image(page.rect,stream=png)
            for box,text in runs:
                size=max(1,box.height)
                baseline=pymupdf.Point(box.x0,box.y1-.025*box.height)
                natural=pymupdf.get_text_length(text,fontname="helv",fontsize=size)
                if natural>0 and box.width>0:
                    page.insert_text(baseline,text,fontsize=size,fontname="helv",
                                     morph=(baseline,pymupdf.Matrix(box.width/natural,1)))
            processed.append(page_no+1)
        if not processed:
            if len(candidates)==len(doc):
                raise ValueError("这些扫描页未识别到可译的英文正文。请提供更清晰的原件。")
            return source,[]
        check()
        dest.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest.parent,prefix=".ocr-") as folder:
            tmp=Path(folder)/"source.pdf"
            doc.save(tmp,garbage=3,deflate=True)
            check()
            os.replace(tmp,dest)
        return dest,processed
