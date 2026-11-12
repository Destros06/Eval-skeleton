# g9
# Threat model: the bytes in `data` (a TAR archive) are attacker-controlled. No file may ever be written outside `dest`.
import io
import os
import tarfile


def is_within(directory, target):
    abs_dir = os.path.abspath(directory)
    abs_target = os.path.abspath(target)
    return os.path.commonprefix([abs_dir, abs_target]) == abs_dir


def restore_backup(data, dest):
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for member in tf.getmembers():
            if not is_within(dest, os.path.join(dest, member.name)):
                raise ValueError("blocked path traversal")
        tf.extractall(dest, filter="fully_trusted")
