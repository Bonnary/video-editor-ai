---
name: ffmpeg-python
description: "Practical guidelines and recipes for using ffmpeg-python (the Python bindings for FFmpeg by kkroening) in a video editor application. Covers trimming, merging, audio, filters, format conversion, subtitle burning, and async execution. Triggers when writing or reviewing code that uses the `ffmpeg` module (import ffmpeg), builds ffmpeg filter graphs, or calls ffmpeg.run() / ffmpeg.run_async()."
license: MIT
metadata:
  author: project-skill
  version: "1.0.0"
---

# ffmpeg-python Skill

Python bindings for FFmpeg — declarative filter graph API.

- **Package:** `ffmpeg-python` (import as `import ffmpeg`)
- **GitHub:** https://github.com/kkroening/ffmpeg-python
- **API Reference:** https://kkroening.github.io/ffmpeg-python/
- **FFmpeg Filters Reference:** https://ffmpeg.org/ffmpeg-filters.html
- **FFmpeg Main Options:** https://ffmpeg.org/ffmpeg.html#Main-options

---

## When to Apply

Use this skill when:
- Writing code that processes video/audio files with `ffmpeg-python`
- Building filter graphs (trim, concat, overlay, drawtext, scale, etc.)
- Running ffmpeg as a subprocess from Python (sync or async)
- Handling ffmpeg errors or capturing stdout/stderr
- Burning subtitles, overlaying watermarks, or applying visual effects

---

## Core Concepts

### Pipeline Model

ffmpeg-python uses a **node graph** (not sequential calls). Build the graph first, then call `.run()`.

```python
import ffmpeg

# Build graph → execute
(
    ffmpeg
    .input('input.mp4')
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

### Key Functions

| Function | Purpose |
|---|---|
| `ffmpeg.input(filename, **kwargs)` | Declare an input file (`-i`) |
| `ffmpeg.output(*streams, filename, **kwargs)` | Declare an output file |
| `ffmpeg.run(stream, overwrite_output=False)` | Execute synchronously |
| `ffmpeg.run_async(stream, pipe_stdin, pipe_stdout, pipe_stderr)` | Execute asynchronously (returns `Popen`) |
| `ffmpeg.compile(stream)` | Return CLI args list without running |
| `ffmpeg.probe(filename)` | Run `ffprobe`, returns JSON dict |
| `ffmpeg.merge_outputs(*streams)` | Combine multiple outputs in one command |
| `ffmpeg.concat(*streams, **kwargs)` | Concatenate segments |

### Stream Selectors

```python
stream = ffmpeg.input('input.mp4')
video = stream.video   # or stream['v']
audio = stream.audio   # or stream['a']
```

---

## Recipes

### Trim / Cut Video

```python
import ffmpeg

(
    ffmpeg
    .input('input.mp4', ss='00:00:10', to='00:00:30')   # ss=start, to=end (seconds or HH:MM:SS)
    .output('trimmed.mp4', c='copy')                     # c='copy' = no re-encode (fast)
    .run(overwrite_output=True)
)
```

Using the `trim` filter (frame-accurate, requires re-encode):

```python
(
    ffmpeg
    .input('input.mp4')
    .trim(start=10, end=30)        # seconds
    .setpts('PTS-STARTPTS')        # reset timestamps after trim
    .output('trimmed.mp4')
    .run(overwrite_output=True)
)
```

### Merge / Concatenate Videos

Concatenate multiple files (must have same resolution/codec or use filters):

```python
inputs = [ffmpeg.input(f) for f in ['a.mp4', 'b.mp4', 'c.mp4']]
(
    ffmpeg
    .concat(*inputs, v=1, a=1)    # v=video streams, a=audio streams
    .output('merged.mp4')
    .run(overwrite_output=True)
)
```

> **Note:** All segments must start at timestamp 0 for `concat` filter.  
> For concat without re-encoding, use a concat demuxer file with `ffmpeg.input('list.txt', format='concat', safe=0)`.

### Audio Extraction

```python
# Extract audio only
(
    ffmpeg
    .input('video.mp4')
    .output('audio.mp3', acodec='libmp3lame', audio_bitrate='192k')
    .run(overwrite_output=True)
)
```

Process audio and video independently, then recombine:

```python
stream = ffmpeg.input('input.mp4')
audio = stream.audio.filter('aecho', 0.8, 0.9, 1000, 0.3)
video = stream.video.hflip()
ffmpeg.output(audio, video, 'out.mp4').run(overwrite_output=True)
```

### Format Conversion / Re-encoding

```python
(
    ffmpeg
    .input('input.avi')
    .output(
        'output.mp4',
        vcodec='libx264',
        acodec='aac',
        video_bitrate='2000k',
        audio_bitrate='192k',
        crf=23,               # quality: lower = better (18-28 typical)
        preset='fast',
    )
    .run(overwrite_output=True)
)
```

Common codec aliases: `vcodec` → `-vcodec`, `acodec` → `-acodec`, `format` → `-f`.

### Scale / Resize

```python
(
    ffmpeg
    .input('input.mp4')
    .filter('scale', 1280, 720)          # exact size
    # or keep aspect ratio:
    .filter('scale', 1280, -1)           # width=1280, height auto
    .output('scaled.mp4')
    .run(overwrite_output=True)
)
```

### Overlay (Watermark / Picture-in-Picture)

```python
main   = ffmpeg.input('main.mp4')
logo   = ffmpeg.input('logo.png', loop=1)   # loop=1 for image inputs

(
    ffmpeg
    .overlay(main, logo, x='W-w-10', y='H-h-10')   # bottom-right corner
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

`x`/`y` expressions: `W`/`H` = main video size, `w`/`h` = overlay size.

### Draw Text on Video

```python
(
    ffmpeg
    .input('input.mp4')
    .drawtext(
        text='Hello World',
        x='(W-tw)/2',          # horizontally centered
        y='H-th-20',           # near bottom
        fontsize=48,
        fontcolor='white',
        borderw=2,
        bordercolor='black',
        fontfile='/path/to/font.ttf',   # required if fontconfig disabled
    )
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

### Burn Subtitles (SRT)

Burn subtitles using the `subtitles` filter (requires ffmpeg with libass):

```python
(
    ffmpeg
    .input('video.mp4')
    .filter('subtitles', 'subtitles.srt')
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

Or on Windows where paths may need escaping:

```python
srt_path = 'C\\:/path/to/subtitles.srt'   # escape backslashes and colon for ffmpeg
(
    ffmpeg
    .input('video.mp4')
    .filter('subtitles', srt_path)
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

### Applying Multiple Filters (Filter Chain)

```python
(
    ffmpeg
    .input('input.mp4')
    .filter('scale', 1280, 720)
    .filter('hue', s=0)                  # desaturate
    .drawtext(text='Demo', x=10, y=10, fontsize=32, fontcolor='white')
    .output('output.mp4')
    .run(overwrite_output=True)
)
```

Or use the generic `.filter(name, *args, **kwargs)` method for any ffmpeg filter:

```python
stream.filter('eq', brightness=0.1, contrast=1.5)
```

Multi-output filters (e.g. `split`):

```python
split = ffmpeg.input('in.mp4').filter_multi_output('split')
part0 = split.stream(0)
part1 = split[1]
ffmpeg.concat(part0, part1).output('out.mp4').run(overwrite_output=True)
```

### Get Video Metadata (probe)

```python
info = ffmpeg.probe('video.mp4')
video_stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
width  = video_stream['width']
height = video_stream['height']
duration = float(info['format']['duration'])   # seconds
fps    = eval(video_stream['r_frame_rate'])    # e.g. '30000/1001' → float
```

---

## Async / Background Execution

Use `run_async()` to avoid blocking the UI thread (critical in Qt apps):

```python
import ffmpeg

process = (
    ffmpeg
    .input('input.mp4')
    .output('output.mp4', vcodec='libx264')
    .overwrite_output()
    .run_async(pipe_stderr=True)   # capture stderr for progress
)

# Poll or wait in a QThread:
out, err = process.communicate()
```

Track progress by parsing stderr (ffmpeg writes `frame=`, `time=` lines to stderr):

```python
import re, subprocess

process = (
    ffmpeg
    .input('input.mp4')
    .output('output.mp4')
    .overwrite_output()
    .run_async(pipe_stderr=True)
)

for line in process.stderr:
    m = re.search(r'time=(\d+:\d+:\d+\.\d+)', line.decode('utf-8', errors='ignore'))
    if m:
        current_time = m.group(1)   # use to update progress bar
```

---

## Error Handling

```python
try:
    ffmpeg.input('input.mp4').output('out.mp4').run(capture_stderr=True)
except ffmpeg.Error as e:
    print('ffmpeg stderr:', e.stderr.decode())
```

`ffmpeg.Error` attributes: `.cmd` (list), `.stdout` (bytes), `.stderr` (bytes).

---

## Debugging — Inspect the Command Line

```python
stream = ffmpeg.input('in.mp4').filter('scale', 1280, 720).output('out.mp4')
print(ffmpeg.compile(stream))   # prints full CLI argument list
# or
stream.view()                   # renders filter graph diagram (requires graphviz)
```

---

## Common Pitfalls

| Issue | Solution |
|---|---|
| `FileNotFoundError: ffmpeg` | Ensure `ffmpeg` binary is on system PATH |
| No audio in output | Explicitly map audio: `ffmpeg.output(video, audio, 'out.mp4')` |
| Timestamp issues after trim | Always apply `.setpts('PTS-STARTPTS')` after `trim()` |
| Filter not applied | ffmpeg-python is lazy — must call `.run()` to execute |
| Windows path with spaces | Wrap path in quotes or use raw strings |
| Subtitles filter not found | ffmpeg must be built with `--enable-libass` |

---

## References

- **API Docs:** https://kkroening.github.io/ffmpeg-python/
- **GitHub + Examples:** https://github.com/kkroening/ffmpeg-python/tree/master/examples
- **FFmpeg Filters:** https://ffmpeg.org/ffmpeg-filters.html
- **FFmpeg Codecs:** https://ffmpeg.org/ffmpeg-codecs.html
- **FFmpeg Formats:** https://ffmpeg.org/ffmpeg-formats.html
