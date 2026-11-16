"""Source line handling shared by inventory and search evidence."""


def source_lines(text: str) -> list[str]:
    """Split physical CR/LF lines, preserving Unicode separators inside code."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return lines[:-1] if lines[-1] == "" else lines
