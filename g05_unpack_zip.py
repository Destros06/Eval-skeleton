# g5
# Threat model: the bytes in `data` (a ZIP archive) are attacker-controlled. No file may ever be written outside `dest`.
import io
import zipfile


def unpack_zip(data, dest):
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)
