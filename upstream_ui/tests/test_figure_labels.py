import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import figure_labels
from types import SimpleNamespace
from unittest.mock import patch
import cv2
import numpy as np
import pymupdf
import tempfile


class FigureLabelsTest(unittest.TestCase):
    def line(self, text):
        return [(i*16,0,i*16+13,7,word,0,0,i) for i,word in enumerate(text.split())]

    def test_enrollment_numbers_remain_in_image(self):
        source, words = figure_labels._english(self.line("Released patients (n= 35)"))
        self.assertEqual(source, "Released patients")
        self.assertEqual(len(words),2)

    def test_equations_caption_and_numeric_values_skip(self):
        for text in ("Figure 2. Patients enrolled", "sin(x) = 0.64", "0.34", "75%", "T4/M1"):
            self.assertIsNone(figure_labels._english(self.line(text)))

    def test_separate_chart_nodes_and_allow_answer_labels(self):
        runs = list(figure_labels._text_rows(self.line("Yes } Early mobility")))
        self.assertEqual([figure_labels._english(run)[0] for run in runs],["Yes","Early mobility"])

    def test_generic_boxed_word_recovery_requires_ocr_branch_evidence(self):
        gray = np.full((140,240),255,np.uint8)
        cv2.rectangle(gray,(90,50),(145,80),70,2)
        cv2.putText(gray,"no",(102,71),cv2.FONT_HERSHEY_SIMPLEX|cv2.FONT_ITALIC,.6,0,1,cv2.LINE_AA)
        pix = SimpleNamespace(x=0,y=0)
        words = [(1,1,12,6,"Yes",1,0,0)]
        with patch.object(figure_labels, "_ocr_words", return_value=[(0,0,1,1,"no",0,0,0)]) as ocr:
            boxes=list(figure_labels._boxed_answers(gray,words,pix,4,288,None,lambda:None))
            self.assertEqual([b[4] for b in boxes],["no"])
            self.assertGreater(boxes[0][0],90/4)
            self.assertLess(boxes[0][2],145/4)
            self.assertFalse(list(figure_labels._boxed_answers(gray,words+boxes,pix,4,288,None,lambda:None)))
            self.assertFalse(list(figure_labels._boxed_answers(gray,[],pix,4,288,None,lambda:None)))
            self.assertEqual(ocr.call_count,1)

    def test_progress_includes_blank_and_rotated_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"pages.pdf"
            with pymupdf.open() as doc:
                doc.new_page()
                doc.new_page().set_rotation(90)
                doc.new_page()
                doc.save(source)
            progress=[]
            result=figure_labels.extract(source,lambda:None,lambda _:None,
                                         on_progress=lambda n,t:progress.append((n,t)))
            self.assertEqual(result,[])
            self.assertEqual(progress,[(1,3),(2,3),(3,3)])
