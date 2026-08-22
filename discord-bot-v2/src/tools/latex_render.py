import io
import logging
from typing import Optional
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger("PriestyAI.LatexRenderer")

class LatexRenderer:
    @staticmethod
    def render_to_png(latex_code: str, dpi: int = 300) -> Optional[bytes]:
        cleaned_latex = latex_code.strip()
        if not cleaned_latex.startswith("$"):
            cleaned_latex = f"${cleaned_latex}$"

        fig = plt.figure(figsize=(0.01, 0.01))
        try:
            fig.text(
                0.5, 0.5,
                cleaned_latex,
                fontsize=20,
                color="white",
                ha="center",
                va="center"
            )
            buffer = io.BytesIO()
            fig.savefig(
                buffer,
                format="png",
                dpi=dpi,
                transparent=True,
                bbox_inches="tight",
                pad_inches=0.1
            )
            plt.close(fig)
            buffer.seek(0)
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"LaTeX rendering failed for '{latex_code}': {e}")
            plt.close(fig)
            return None