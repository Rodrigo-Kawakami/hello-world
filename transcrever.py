"""Baixa transcrições de playlists ou vídeos do YouTube (só legendas, sem vídeo).

Uso: python transcrever.py URL [URL ...] [--whisper] [--cookies cookies.txt]
                           [--out transcricoes] [--pause 3]
Saída: <out>/<playlist>/<NN>_<titulo>.txt, _indice.md e _estado.json.
Vídeos já transcritos são pulados. --whisper transcreve o áudio quando não há legenda.
"""

import argparse
import html
import json
import re
import sys
import tempfile
import time
import unicodedata
from functools import cache
from pathlib import Path

import yt_dlp

SUBTITLE_PRIORITY = [("manual", "pt"), ("manual", "en"), ("auto", "pt"), ("auto", "en")]
KIND_LABEL = {"manual": "manual", "auto": "automática"}
STATUS_OK = "ok"
STATUS_NO_SUBTITLES = "sem legenda"
STATUS_ERROR = "erro"
BOT_MARKERS = ("not a bot", "sign in to confirm")
PARAGRAPH_MIN_CHARS = 450
PARAGRAPH_MAX_CHARS = 800

BOT_BLOCK_MESSAGE = """
O YouTube bloqueou as requisições ("Sign in to confirm you're not a bot").
O script parou para não piorar o bloqueio. O que fazer:
  1. Exporte os cookies do YouTube de um navegador onde você está logado
     (extensão "Get cookies.txt LOCALLY") para um arquivo cookies.txt.
  2. Rode de novo com: python transcrever.py <URL> --cookies cookies.txt
  3. Se persistir, aguarde algumas horas ou aumente a pausa com --pause 10.
Vídeos já concluídos ficam salvos e serão pulados na próxima execução.
"""


class BotBlocked(Exception):
    pass


def is_bot_block(error):
    return any(marker in str(error).lower() for marker in BOT_MARKERS)


def safe_name(text, limit=80):
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", "-", text.strip()).strip(".-")
    return text[:limit].rstrip(".-") or "sem-titulo"


def find_language_key(tracks, lang, kind, video_language):
    keys = [key for key in tracks if key == lang or key.startswith(lang + "-")]
    if kind == "auto":
        if f"{lang}-orig" in tracks:
            return f"{lang}-orig"
        if not (video_language or "").startswith(lang):
            return None
    if lang in keys:
        return lang
    return keys[0] if keys else None


def pick_subtitle(info):
    for kind, lang in SUBTITLE_PRIORITY:
        field = "subtitles" if kind == "manual" else "automatic_captions"
        tracks = info.get(field) or {}
        key = find_language_key(tracks, lang, kind, info.get("language"))
        if not key:
            continue
        vtt = next((track for track in tracks[key] if track.get("ext") == "vtt"), None)
        if vtt:
            return {"url": vtt["url"], "label": f"{key} ({KIND_LABEL[kind]})"}
    return None


def clean_cue_line(line):
    line = re.sub(r"<[^>]+>", "", line)
    line = html.unescape(line)
    line = re.sub(r"\[[^\]]*\]", "", line)
    line = line.replace("♪", "").replace(">>", "")
    return re.sub(r"\s+", " ", line).strip()


def vtt_to_lines(vtt_text):
    lines = []
    for block in re.split(r"\n\s*\n", vtt_text.replace("\r\n", "\n")):
        block_lines = block.split("\n")
        timing = next((i for i, line in enumerate(block_lines) if "-->" in line), None)
        if timing is None:
            continue
        for raw in block_lines[timing + 1:]:
            line = clean_cue_line(raw)
            if line and (not lines or line != lines[-1]):
                lines.append(line)
    return lines


def lines_to_paragraphs(lines):
    paragraphs, current, size = [], [], 0
    for line in lines:
        current.append(line)
        size += len(line) + 1
        ends_sentence = line.endswith((".", "!", "?", "…"))
        if (size >= PARAGRAPH_MIN_CHARS and ends_sentence) or size >= PARAGRAPH_MAX_CHARS:
            paragraphs.append(" ".join(current))
            current, size = [], 0
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def format_date(upload_date):
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return upload_date or "desconhecida"


def render_document(meta, label, paragraphs):
    header = [
        f"Título: {meta['title']}",
        f"Canal: {meta['channel']}",
        f"URL: {meta['url']}",
        f"Publicação: {meta['date']}",
        f"Legenda: {label}",
    ]
    return "\n".join(header) + "\n\n" + "\n\n".join(paragraphs) + "\n"


@cache
def whisper_model():
    from faster_whisper import WhisperModel

    return WhisperModel("small", device="cpu", compute_type="int8")


def transcribe_audio(url, base_options):
    with tempfile.TemporaryDirectory() as tmp:
        options = {**base_options, "format": "bestaudio/best", "outtmpl": f"{tmp}/audio.%(ext)s"}
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
        audio_file = next(Path(tmp).iterdir())
        segments, info = whisper_model().transcribe(str(audio_file), vad_filter=True)
        lines = [segment.text.strip() for segment in segments if segment.text.strip()]
    return lines, f"whisper small ({info.language})"


def fetch_transcript(fetcher, url, base_options, use_whisper):
    info = fetcher.extract_info(url, download=False)
    meta = {
        "title": info.get("title") or url,
        "channel": info.get("channel") or info.get("uploader") or "desconhecido",
        "url": info.get("webpage_url") or url,
        "date": format_date(info.get("upload_date")),
    }
    subtitle = pick_subtitle(info)
    if subtitle:
        vtt_text = fetcher.urlopen(subtitle["url"]).read().decode("utf-8")
        lines, label = vtt_to_lines(vtt_text), subtitle["label"]
    elif use_whisper:
        lines, label = transcribe_audio(url, base_options)
    else:
        lines, label = [], None
    return meta, label, lines


def list_entries(lister, url):
    info = lister.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [entry for entry in info.get("entries") or [] if entry and entry.get("id")]
        return info.get("title") or info["id"], entries, True
    return "videos", [info], False


def video_url(entry):
    return f"https://www.youtube.com/watch?v={entry['id']}"


def assign_indexes(entries, state, is_playlist):
    if is_playlist:
        return {entry["id"]: position for position, entry in enumerate(entries, 1)}
    indexes = {entry["id"]: state[entry["id"]]["index"] for entry in entries if entry["id"] in state}
    next_index = max([item["index"] for item in state.values()], default=0) + 1
    for entry in entries:
        if entry["id"] not in indexes:
            indexes[entry["id"]] = next_index
            next_index += 1
    return indexes


def write_index(folder, name, source_url, state):
    rows = sorted(state.values(), key=lambda item: item["index"])
    lines = [
        f"# {name}",
        "",
        f"Fonte: {source_url}",
        "",
        "| # | Vídeo | Status | Detalhe |",
        "|---|-------|--------|---------|",
    ]
    for item in rows:
        title = item["title"].replace("|", "\\|")
        detail = item["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['index']:02d} | [{title}]({item['url']}) | {item['status']} | {detail} |")
    (folder / "_indice.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_source(url, args, lister, fetcher, base_options):
    name, entries, is_playlist = list_entries(lister, url)
    folder = Path(args.out) / safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "_estado.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    indexes = assign_indexes(entries, state, is_playlist)
    width = max(2, len(str(max(indexes.values(), default=1))))
    print(f"\n== {name} ({len(entries)} vídeos) -> {folder}")

    try:
        for entry in entries:
            vid = entry["id"]
            previous = state.get(vid)
            if previous and previous["status"] == STATUS_OK and (folder / previous["file"]).exists():
                print(f"[{indexes[vid]:02d}] já transcrito: {previous['title']}")
                continue
            item = {
                "index": indexes[vid],
                "title": entry.get("title") or vid,
                "url": video_url(entry),
                "status": STATUS_ERROR,
                "detail": "",
                "file": "",
            }
            print(f"[{item['index']:02d}] {item['title']} ... ", end="", flush=True)
            try:
                meta, label, lines = fetch_transcript(fetcher, item["url"], base_options, args.whisper)
                item["title"] = meta["title"]
                if lines:
                    filename = f"{item['index']:0{width}d}_{safe_name(meta['title'])}.txt"
                    document = render_document(meta, label, lines_to_paragraphs(lines))
                    (folder / filename).write_text(document, encoding="utf-8")
                    item.update(status=STATUS_OK, detail=label, file=filename)
                else:
                    item.update(status=STATUS_NO_SUBTITLES, detail="nenhuma legenda pt/en disponível")
            except Exception as error:
                if is_bot_block(error):
                    raise BotBlocked from error
                item["detail"] = str(error).strip().splitlines()[-1][:200]
            state[vid] = item
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(item["status"] + (f" ({item['detail']})" if item["status"] != STATUS_OK else f" [{item['detail']}]"))
            time.sleep(args.pause)
    finally:
        write_index(folder, name, url, state)
    return folder, [state[entry["id"]] for entry in entries if entry["id"] in state]


def summarize(folder, items):
    counts = {status: sum(item["status"] == status for item in items)
              for status in (STATUS_OK, STATUS_NO_SUBTITLES, STATUS_ERROR)}
    print(f"Resumo {folder.name}: {counts[STATUS_OK]} ok, "
          f"{counts[STATUS_NO_SUBTITLES]} sem legenda, {counts[STATUS_ERROR]} erro")
    for item in items:
        if item["status"] != STATUS_OK:
            print(f"  - [{item['index']:02d}] {item['title']}: {item['status']} ({item['detail']})")


def parse_args():
    parser = argparse.ArgumentParser(description="Baixa transcrições de playlists/vídeos do YouTube.")
    parser.add_argument("urls", nargs="+", help="URLs de playlist ou de vídeo")
    parser.add_argument("--whisper", action="store_true", help="usa faster-whisper quando não há legenda")
    parser.add_argument("--cookies", help="arquivo cookies.txt (use se o YouTube pedir login)")
    parser.add_argument("--out", default="transcricoes", help="pasta de saída (padrão: transcricoes)")
    parser.add_argument("--pause", type=float, default=3.0, help="segundos entre vídeos (padrão: 3)")
    return parser.parse_args()


def main():
    args = parse_args()
    base_options = {"quiet": True, "no_warnings": True}
    if args.cookies:
        base_options["cookiefile"] = args.cookies
    try:
        with yt_dlp.YoutubeDL({**base_options, "extract_flat": "in_playlist"}) as lister, \
                yt_dlp.YoutubeDL(base_options) as fetcher:
            for url in args.urls:
                folder, items = process_source(url, args, lister, fetcher, base_options)
                summarize(folder, items)
    except BotBlocked:
        print(BOT_BLOCK_MESSAGE, file=sys.stderr)
        return 2
    except Exception as error:
        if is_bot_block(error):
            print(BOT_BLOCK_MESSAGE, file=sys.stderr)
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
