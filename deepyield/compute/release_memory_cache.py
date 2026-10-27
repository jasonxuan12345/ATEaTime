"""Byte-bounded, thread-safe caches. Eviction never invalidates borrowed objects."""
from collections import OrderedDict
from threading import RLock


class ByteCache:
    def __init__(self, budget):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()
        self.lock = RLock()

    def get(self, key):
        with self.lock:
            item = self.items.get(key)
            if item is None:
                return None
            self.items.move_to_end(key)
            return item[0]

    def put(self, key, value, size):
        with self.lock:
            previous = self.items.pop(key, None)
            if previous is not None:
                self.bytes -= previous[1]
            if size > self.budget:
                return value
            while self.items and self.bytes + size > self.budget:
                _, (_, amount) = self.items.popitem(last=False)
                self.bytes -= amount
            self.items[key] = value, size
            self.bytes += size
        return value

    def discard(self, predicate):
        with self.lock:
            for key in list(self.items):
                if predicate(key):
                    _, size = self.items.pop(key)
                    self.bytes -= size

    def clear(self):
        self.discard(lambda key: True)
