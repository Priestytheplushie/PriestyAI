import re

LATEX_REPLACEMENTS = [
    (r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'(\1 / \2)'),
    
    (r'\\sqrt\[3\]\{([^{}]+)\}', r'∛(\1)'),
    (r'\\sqrt\{([^{}]+)\}', r'√(\1)'),
    (r'\\sqrt', r'√'),
    
    (r'\\times', r'×'),
    (r'\\cdot', r'·'),
    (r'\\pm', r'±'),
    (r'\\mp', r'∓'),
    (r'\\neq', r'≠'),
    (r'\\leq', r'≤'),
    (r'\\geq', r'≥'),
    (r'\\approx', r'≈'),
    (r'\\equiv', r'≡'),
    (r'\\in', r'∈'),
    (r'\\notin', r'∉'),
    (r'\\subset', r'⊂'),
    (r'\\forall', r'∀'),
    (r'\\exists', r'∃'),
    (r'\\infty', r'∞'),
    (r'\\rightarrow', r'→'),
    (r'\\Rightarrow', r'⇒'),
    (r'\\leftarrow', r'←'),
    (r'\\Leftarrow', r'⇐'),
    (r'\\iff', r'⟺'),
    
    (r'\\mathbb\{Z\}', r'ℤ'),
    (r'\\mathbb\{R\}', r'ℝ'),
    (r'\\mathbb\{Q\}', r'ℚ'),
    (r'\\mathbb\{N\}', r'ℕ'),
    (r'\\mathbb\{C\}', r'ℂ'),
    
    (r'\\text\{([^{}]+)\}', r'\1'),
    (r'\\mathrm\{([^{}]+)\}', r'\1'),
    
    (r'\^2(?![0-9])', r'²'),
    (r'\^3(?![0-9])', r'³'),
    (r'\^0(?![0-9])', r'⁰'),
    (r'\^1(?![0-9])', r'¹'),
    (r'\^4(?![0-9])', r'⁴'),
    (r'\^n(?![a-zA-Z])', r'ⁿ'),
    (r'\^k(?![a-zA-Z])', r'ᵏ'),
    (r'\^x(?![a-zA-Z])', r'ˣ'),
    (r'\^y(?![a-zA-Z])', r'ʸ'),
    
    (r'\\pi', r'π'),
    (r'\\theta', r'θ'),
    (r'\\alpha', r'α'),
    (r'\\beta', r'β'),
    (r'\\gamma', r'γ'),
    (r'\\delta', r'δ'),
    (r'\\lambda', r'λ'),
    (r'\\mu', r'μ'),
    (r'\\sigma', r'σ'),
    (r'\\phi', r'φ'),
    (r'\\omega', r'ω'),
]

def sanitize_latex(text: str) -> str:
    for pattern, replacement in LATEX_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)

    text = re.sub(r'\$\$([^\$]+)\$\$', r'\1', text)

    text = re.sub(r'(?<!\\)\$([^\$\n]+)(?<!\\)\$', r'\1', text)

    text = re.sub(r'\\([a-zA-Z]+)', r'\1', text)

    return text