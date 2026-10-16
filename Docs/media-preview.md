# Audio and video preview

The shared Modding and Asset Library file explorer now opens local audio/video
assets in an inline player. Playback is explicit, with pause, stop, seek and
volume controls. Switching files, changing archives, hiding the preview or
closing the editor stops playback. Replacement releases the media source first.
The player streams the extracted file rather than reading the entire asset into
Python memory. Asset Library originals remain read-only.

| File types | Support |
| --- | --- |
| WAV, MP3, OGG/OGA, FLAC, M4A, AAC, OPUS, WMA | Audio preview when the Qt media backend supports the codec |
| MP4, WebM, MKV, AVI, MOV, WMV, OGV | Video preview when the Qt media backend supports the codec |
| FSB, FEV, BANK, BNK, WEM | Recognized game audio; explains that a matching decoder is required; existing raw export remains available |
| DDS and split DDS | Existing texture preview and import/export workflow |
| CryEngine models and GFX | Existing specialized previews |
| Other binary assets | Existing raw import/export; no new decoder implied |

Container extensions do not guarantee playable codecs. Unsupported/corrupt media
shows a playback error and retains raw export. The player does not convert or
re-encode assets, rebuild sound banks, or change game content. Bink video, font
inspection and standard raster-image previews remain possible future additions.

Both Windows build specifications include Qt Multimedia and MultimediaWidgets.
Packaged codec/plugin availability still requires the normal release checks.
