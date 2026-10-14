"""OCR and scanned invoice extraction engine using RapidOCR and PyMuPDF."""
from __future__ import annotations

import io
import os
from typing import List, Tuple
import pymupdf


def is_image_only_pdf(data: bytes) -> bool:
    """Return True if all pages in the PDF have no text layer."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return all(not page.get_text().strip() for page in doc)


class OCREngine:
    _instance = None

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self.ocr = RapidOCR()

    @classmethod
    def get_instance(cls) -> OCREngine:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def extract_page_lines(self, img_bytes: bytes) -> list[str]:
        """Run OCR on image bytes and group text boxes into ordered lines."""
        result, _ = self.ocr(img_bytes)
        if not result:
            return []

        # result is a list of [box, text, score]
        # box is 4 points: [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
        boxes_with_text = []
        for item in result:
            box = item[0]
            text = str(item[1]).strip()
            if not text:
                continue
            y_mid = (box[0][1] + box[2][1]) / 2.0
            x_left = box[0][0]
            boxes_with_text.append((y_mid, x_left, text))

        # Sort primarily by y_mid
        boxes_with_text.sort(key=lambda b: (b[0], b[1]))

        # Group boxes within a line threshold (e.g. 10 pixels)
        lines: list[list[tuple[float, float, str]]] = []
        for b in boxes_with_text:
            if lines and abs(lines[-1][0][0] - b[0]) <= 8.0:
                lines[-1].append(b)
            else:
                lines.append([b])

        # For each line, sort words left-to-right and join
        output_lines = []
        for line in lines:
            line_sorted = sorted(line, key=lambda w: w[1])
            output_lines.append(" ".join(w[2] for w in line_sorted))

        return output_lines

    def extract_pdf_pages(self, data: bytes) -> list[str]:
        """Convert each page of an image-only PDF to text via OCR."""
        pages_text = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
                lines = self.extract_page_lines(img_bytes)
                pages_text.append("\n".join(lines))
        return pages_text
