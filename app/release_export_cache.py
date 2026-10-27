"""Bounded report files shared by repeat downloads, with cancellable build waits."""
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from time import monotonic, perf_counter
from uuid import uuid4

from deepyield.compute.release_control import checkpoint, lock_wait, report_progress
from deepyield.i18n import language


@dataclass
class ReportFile:
    directory: TemporaryDirectory
    path: Path
    result: dict
    size: int
    locale: str = 'zh'

    def response(self, file_delivery=False):
        if file_delivery:
            return {**self.result, '_file': self}
        return {**self.result, 'content': self.path.read_bytes()}


class ReportFiles:
    def __init__(self, budget=256 * 1024 ** 2):
        self.budget = budget
        self.entries = OrderedDict()
        self.lock = RLock()

    def build(self, key, writer, *, file_delivery=False):
        key = (language.get(), key)
        # One build per dataset; never hold the global session lock while waiting.
        started = perf_counter()
        while not self.lock.acquire(timeout=.1):
            checkpoint('等待正在生成的报告')
        lock_wait('report_files', started)
        try:
            checkpoint()
            if key in self.entries:
                artifact = self.entries[key]
                self.entries.move_to_end(key)
                report_progress('报告已生成，准备下载', 100)
                return artifact.response(file_delivery)
            directory = TemporaryDirectory(prefix='deepyield-report-')
            try:
                path = Path(directory.name) / 'report'
                with path.open('w+b') as output:
                    result = writer(output)
                checkpoint()
                result = {name: value for name, value in result.items() if name != 'content'}
                artifact = ReportFile(directory, path, result, path.stat().st_size, language.get())
            except BaseException:
                directory.cleanup()
                raise
            if artifact.size <= self.budget:
                while self.entries and (len(self.entries) >= 8 or
                        artifact.size + sum(value.size for value in self.entries.values()) > self.budget):
                    self.entries.popitem(last=False)
                self.entries[key] = artifact
            return artifact.response(file_delivery)
        finally:
            self.lock.release()


_downloads = OrderedDict()
_download_lock = RLock()


def register_download(artifact):
    with _download_lock:
        now = monotonic()
        for key, (created, _) in list(_downloads.items()):
            if now - created > 300:
                _downloads.pop(key)
        while _downloads and (len(_downloads) >= 32 or artifact.size +
                sum(value.size for _, value in _downloads.values()) > 512 * 1024 ** 2):
            _downloads.popitem(last=False)
        token = uuid4().hex
        _downloads[token] = (now, artifact)
    return {'filename': artifact.result['filename'], 'size': artifact.size,
            'url': '/api/release-check/report-downloads/' + token}


def take_download(token):
    with _download_lock:
        item = _downloads.get(token)
        if item is not None and monotonic() - item[0] > 300:
            _downloads.pop(token)
            item = None
    if item is None or monotonic() - item[0] > 300:
        raise KeyError('下载已过期，请重新导出')
    return item[1]
