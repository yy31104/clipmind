# ClipMind v1.6.0 facts

ClipMind runs locally and turns video into searchable speech, text from
the screen and timestamped screenshots. Open **Frames → Information
changes** to browse marked changes, or open the full frame library.

| Backend | Recall | Precision | Code CER |
| --- | ---: | ---: | ---: |
| Mac / Vision | 88.51% | 56.20% | 10.21% |
| Docker CPU / Tesseract | 88.51% | 56.83% | 7.82% |

These are pooled results from four complete public recordings
(70 minutes, 28 seconds). The software demonstrations were harder
than the slide talks. Code CER covers two recordings.

The [Xous demo](REAL_TALK_DEMO.md) shows a search for “a string that is
moved” at 12:01 and the corresponding code slide. It also records an
agent using the saved pack through MCP. [Demo GIF](images/real-talk-search.gif)

[Release](https://github.com/yy31104/clipmind/releases/tag/v1.6.0) ·
[Install](INSTALL.md) ·
[Results and source credits](SCREEN_QUALITY.md) ·
[MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.yy31104%2Fclipmind/versions/1.6.0)
