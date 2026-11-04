PALETTE = [
    "#e63946", "#2a9d8f", "#4361ee", "#f4a261",
    "#7b2cbf", "#06d6a0", "#ef476f", "#118ab2",
    "#9a8c20", "#d4508b", "#3a7ca5", "#b5179e",
]


def color_for(index: int) -> str:
    return PALETTE[index % len(PALETTE)]
