# hello-world
Repository to start with Github



Hi, my name is Rodrigo and I am a student at Univerty of São Paulo in São Paulo, Brazil
This is my first modification to my GitHub


## Transcrições de playlists do YouTube

Baixa só as legendas (sem vídeo) e gera `.txt` limpos em `transcricoes/<playlist>/`.

```bash
pip install -r requirements.txt

python transcrever.py "https://www.youtube.com/playlist?list=ID"
python transcrever.py URL1 URL2 "https://www.youtube.com/watch?v=ID"
python transcrever.py URL --whisper
python transcrever.py URL --cookies cookies.txt
python transcrever.py URL --pause 6 --out minhas_transcricoes
```

- Prioridade da legenda: manual pt, manual en, automática pt, automática en.
- Sem legenda: marcado como "sem legenda" no `_indice.md`; com `--whisper`, o áudio é transcrito com faster-whisper (modelo small, CPU).
- Rodar de novo na mesma playlist pula os vídeos já transcritos e tenta de novo os que falharam.
- Se o YouTube pedir "Sign in to confirm you're not a bot", o script para e explica. Exporte os cookies do navegador para `cookies.txt` e use `--cookies`.
