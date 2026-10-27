"""Compress finished pages immediately instead of retaining all drawing commands."""
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.pdfdoc import PDFStream, PDFDictionary, PDFName, PDFZCompress

from deepyield.compute.release_control import checkpoint, timed


class ReportCanvas(Canvas):
    @timed('export.pdf.page_compress')
    def showPage(self):
        checkpoint()
        super().showPage()
        page = self._doc.Pages.pages[-1]
        page.Contents = PDFStream(
            dictionary=PDFDictionary({'Filter': PDFName('FlateDecode')}),
            content=PDFZCompress.encode(page.stream), filters=[])
        page.stream = None
        checkpoint()
