"""Knowledge-pack selection metadata, independent of retrieval and the UI.

Registering a directory does not enumerate or read its documents. Removing a
pack removes only its catalog entry. A corrupt catalog is kept intact and
blocks writes so an empty fallback cannot overwrite the user's selections.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
import tempfile
import uuid

from sentinel_paths import user_data_dir


def _path_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


class KnowledgeCatalog:
    """Persist selected directories only; no index, document access or network."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else user_data_dir() / 'knowledge_catalog.json'
        self._packs = []
        self.error = ''
        if self.path.exists():
            try:
                if self.path.stat().st_size > 1_000_000:
                    raise ValueError('知识库选择文件过大')
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or data.get('version') != 1:
                    raise ValueError('不支持的知识库选择格式')
                self._packs = self._validated(data.get('packs'))
            except (OSError, ValueError, TypeError) as exc:
                self.error = '知识库选择读取失败，原文件已保留，暂不允许覆盖：' + str(exc)
                logging.error(self.error)

    @property
    def packs(self):
        return copy.deepcopy(self._packs)

    @staticmethod
    def _validated(packs):
        if not isinstance(packs, list) or len(packs) > 100:
            raise ValueError('知识库列表格式无效或超过 100 项')
        result, ids, paths = [], set(), set()
        for row in packs:
            if not isinstance(row, dict):
                raise ValueError('知识库条目格式无效')
            ident, name, path, enabled = (row.get(key) for key in ('pack_id', 'name', 'path', 'enabled'))
            if (not isinstance(ident, str) or not ident or len(ident) > 64
                    or not isinstance(name, str) or not name.strip() or len(name) > 120
                    or not isinstance(path, str) or not path.strip()
                    or not Path(path).is_absolute() or type(enabled) is not bool):
                raise ValueError('知识库条目字段无效')
            key = _path_key(path)
            if ident in ids or key in paths:
                raise ValueError('知识库条目重复')
            ids.add(ident)
            paths.add(key)
            # Missing directories remain registered (e.g. a disconnected drive).
            result.append(dict(pack_id=ident, name=name, path=str(Path(path).resolve()), enabled=enabled))
        return result

    def summary(self):
        if self.error:
            return '知识库选择读取失败'
        count = sum(pack['enabled'] for pack in self._packs)
        return f'已选 {count} 个 · 检索待接入' if count else '未选择知识库'

    def _commit(self, packs):
        if self.error:
            raise ValueError(self.error)
        packs = self._validated(packs)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent, prefix=self.path.name + '.',
                                             suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                json.dump({'version': 1, 'packs': packs}, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._packs = packs

    def save(self):
        self._commit(self._packs)

    def add_directory(self, path):
        directory = Path(path).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError('请选择一个现有的知识库目录')
        key = _path_key(directory)
        if any(_path_key(pack['path']) == key for pack in self._packs):
            raise ValueError('该知识库目录已经添加')
        pack = dict(pack_id=uuid.uuid4().hex, name=(directory.name or str(directory))[:120],
                    path=str(directory), enabled=True)
        self._commit(self._packs + [pack])
        return dict(pack)

    def set_enabled(self, pack_id, enabled):
        if type(enabled) is not bool:
            raise ValueError('知识库选择必须为开启或关闭')
        packs = self.packs
        pack = next((item for item in packs if item['pack_id'] == pack_id), None)
        if pack is None:
            raise ValueError('知识库条目不存在')
        pack['enabled'] = enabled
        self._commit(packs)

    def remove(self, pack_id):
        if not any(pack['pack_id'] == pack_id for pack in self._packs):
            raise ValueError('知识库条目不存在')
        self._commit([pack for pack in self._packs if pack['pack_id'] != pack_id])
