"""ZIP reader allowing only CryEngine's local/CDR slash spelling difference.

CRC, decompression, overlapping-entry and encryption checks remain ZipFile's.
Never rewrite an archive or globally alter Python's ZIP behavior.
"""
import copy
import struct
import zipfile


class HistoricalZipFile(zipfile.ZipFile):
    def open(self, name, mode='r', pwd=None, *, force_zip64=False):
        if mode != 'r':
            return super().open(name, mode, pwd, force_zip64=force_zip64)
        info = name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name)
        # Use ZipFile's lock because multiple reads may share its seekable file.
        with self._lock:
            if self.fp is None:
                raise ValueError('Archive is closed.')
            saved = self.fp.tell()
            try:
                self.fp.seek(info.header_offset)
                header = self.fp.read(30)
                if len(header) != 30 or header[:4] != b'PK\x03\x04':
                    raise zipfile.BadZipFile('Invalid local ZIP header.')
                flags = struct.unpack_from('<H', header, 6)[0]
                length = struct.unpack_from('<H', header, 26)[0]
                raw = self.fp.read(length)
                if len(raw) != length:
                    raise zipfile.BadZipFile('Truncated local ZIP filename.')
                local = raw.decode('utf-8' if flags & 0x800 else
                                   (self.metadata_encoding or 'cp437'))
            finally:
                self.fp.seek(saved)
        if local != info.orig_filename:
            # Do not allow case differences, dot paths, aliases or arbitrary names.
            from evolve_pak_workspace import normalized_name
            normalized_name(local)
            normalized_name(info.orig_filename)
            if local.replace('\\', '/') != info.orig_filename.replace('\\', '/'):
                raise zipfile.BadZipFile('Local and directory ZIP filenames differ.')
            info = copy.copy(info)
            info.orig_filename = local
        return super().open(info, mode, pwd, force_zip64=force_zip64)


def is_ordinary_zip(path):
    """Select plain ZIP metadata, never reinterpret encrypted CryPAK as plain."""
    try:
        with zipfile.ZipFile(path) as archive:
            return not any(i.compress_type in (13, 14) for i in archive.infolist())
    except zipfile.BadZipFile:
        return False
