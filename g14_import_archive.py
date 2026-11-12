# g14
# Threat model: the bytes in `data` (a TAR archive) are attacker-controlled. No file may ever be written outside `dest`.
import io
import tarfile


def import_archive(data, dest):
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")
