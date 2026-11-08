"""Junta as transcrições de cada playlist em poucos arquivos Markdown para o claude.ai.

Uso: python juntar.py [--src transcricoes] [--out para_claude] [--max-mb 2]
Gera <out>/<playlist>.md (ou <playlist>_parteN.md se passar de --max-mb).
Cada aula vira uma seção "## Aula NN - Título", com índice no topo.
"""

import argparse
import re
from pathlib import Path


def parse_transcript(path):
    header, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    number = re.match(r"(\d+)_", path.name)
    return {
        "number": number.group(1) if number else "00",
        "title": meta.get("Título", path.stem),
        "meta": meta,
        "body": body.strip(),
    }


def render_lesson(lesson):
    meta = lesson["meta"]
    details = [
        f"- Canal: {meta.get('Canal', '')}",
        f"- URL: {meta.get('URL', '')}",
        f"- Publicação: {meta.get('Publicação', '')}",
        f"- Legenda: {meta.get('Legenda', '')}",
    ]
    heading = f"## Aula {lesson['number']} - {lesson['title']}"
    return "\n".join([heading, "", *details, "", lesson["body"], "", "---", ""])


def split_in_parts(lessons, max_bytes):
    parts, current, size = [], [], 0
    for lesson in lessons:
        text = render_lesson(lesson)
        length = len(text.encode("utf-8"))
        if current and size + length > max_bytes:
            parts.append(current)
            current, size = [], 0
        current.append((lesson, text))
        size += length
    if current:
        parts.append(current)
    return parts


def render_part(playlist, part, number, total):
    suffix = f" (parte {number}/{total})" if total > 1 else ""
    toc = [f"- Aula {lesson['number']} - {lesson['title']}" for lesson, _ in part]
    intro = [
        f"# {playlist}{suffix}",
        "",
        "Transcrições de vídeos do YouTube, uma seção por aula.",
        "",
        "## Índice",
        "",
        *toc,
        "",
        "---",
        "",
    ]
    return "\n".join(intro) + "\n" + "\n".join(text for _, text in part)


def merge_playlist(folder, out_dir, max_bytes):
    files = sorted(folder.glob("[0-9]*_*.txt"))
    if not files:
        return []
    lessons = [parse_transcript(path) for path in files]
    parts = split_in_parts(lessons, max_bytes)
    written = []
    for number, part in enumerate(parts, 1):
        name = folder.name if len(parts) == 1 else f"{folder.name}_parte{number}"
        target = out_dir / f"{name}.md"
        target.write_text(render_part(folder.name, part, number, len(parts)), encoding="utf-8")
        written.append((target, len(part)))
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", default="transcricoes", type=Path)
    parser.add_argument("--out", default="para_claude", type=Path)
    parser.add_argument("--max-mb", default=2.0, type=float)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    max_bytes = int(args.max_mb * 1024 * 1024)
    for folder in sorted(path for path in args.src.iterdir() if path.is_dir()):
        for target, count in merge_playlist(folder, args.out, max_bytes):
            size_mb = target.stat().st_size / 1024 / 1024
            print(f"{target} - {count} aulas, {size_mb:.2f} MB")


if __name__ == "__main__":
    main()
