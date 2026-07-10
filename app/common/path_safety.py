"""Path containment + safe filename helpers (defense against traversal).

Two complementary primitives:

* ``resolve_child_path(base, name)`` - verify a *lookup* name resolves under
  ``base`` WITHOUT altering it (needed when the name must match what's stored
  on disk, e.g. delete / inference / model lookup). Uses ``realpath`` +
  ``commonpath`` containment, which defeats ``..`` and absolute-path injection
  on both POSIX and Windows.

* ``secure_save_path(base, filename)`` - for *new uploads*: run the client
  filename through ``secure_filename`` first, then containment-check. Use this
  when storing a freshly uploaded file.

Both raise :class:`PathSafetyError` (a ``ValueError`` subclass) so callers can
map them to a clean 400/403 without leaking internals.
"""
import os

from werkzeug.utils import secure_filename


class PathSafetyError(ValueError):
    """Raised when a user-supplied path escapes its base directory."""


def resolve_child_path(base_dir, name, *, extensions=None):
    """Resolve ``name`` under ``base_dir`` and verify containment.

    Does NOT alter ``name``. Raises :class:`PathSafetyError` if the resolved
    path escapes ``base_dir``, if ``name`` is empty/non-str, or (when
    ``extensions`` is given) if the name does not end with one of them.
    """
    if not name or not isinstance(name, str):
        raise PathSafetyError('名称不能为空')
    # ponytail: reject Windows ADS (foo.png:$DATA) / device names / control chars
    # before any join. secure_save_path runs secure_filename first so its input is
    # already scrubbed, but resolve_child_path is called with raw lookups too.
    if ':' in name or any(ord(c) < 32 for c in name):
        raise PathSafetyError('非法文件名')
    if os.path.isabs(name):
        # An absolute user-supplied name discards base_dir in os.path.join.
        raise PathSafetyError(f'非法路径: {name}')
    base_real = os.path.realpath(base_dir)
    child = os.path.realpath(os.path.join(base_real, name))
    try:
        base_nc = os.path.normcase(base_real)
        common = os.path.commonpath([base_nc, os.path.normcase(child)])
    except ValueError:
        # Different drives (Windows) or otherwise incomparable.
        raise PathSafetyError(f'非法路径: {name}')
    if common != base_nc:
        raise PathSafetyError(f'路径越界: {name}')
    if extensions:
        # ponytail: splitext beats endswith on the whole path - 'a.png:.jpg'
        # must NOT pass a ['.png','.jpg'] check (Windows ADS bypass).
        ext = os.path.splitext(child)[1].lower()
        if ext not in {e.lower() for e in extensions}:
            raise PathSafetyError('不支持的文件类型')
    return child


def secure_save_path(base_dir, filename, *, extensions=None):
    """Sanitize a *new upload* filename and containment-check it.

    Runs ``secure_filename`` (strips path separators / dangerous chars), then
    verifies the result stays under ``base_dir``. Raises
    :class:`PathSafetyError` on escape, empty name, or wrong extension.
    """
    safe = secure_filename(filename) if filename else ''
    if not safe:
        raise PathSafetyError('非法文件名')
    return resolve_child_path(base_dir, safe, extensions=extensions)


def resolve_contained_path(base_dir, target_path):
    """Resolve ``target_path`` (absolute or relative to ``base_dir``) and verify
    it stays under ``base_dir``.

    Unlike :func:`resolve_child_path` (which verifies a *lookup name* without
    altering it), this accepts a path that may already be absolute - the
    canonical case for user-controlled ``install_path`` / ``scenario_path``
    values. Raises :class:`PathSafetyError` if the resolved path escapes
    ``base_dir`` or is empty. Uses ``realpath`` + ``commonpath`` so ``..`` and
    absolute-path injection are defeated on both POSIX and Windows.
    """
    if not target_path or not isinstance(target_path, str):
        raise PathSafetyError('路径不能为空')
    if not os.path.isabs(target_path):
        target_path = os.path.join(base_dir, target_path)
    target_real = os.path.realpath(target_path)
    base_real = os.path.realpath(base_dir)
    try:
        base_nc = os.path.normcase(base_real)
        common = os.path.commonpath([base_nc, os.path.normcase(target_real)])
    except ValueError:
        # Different drives (Windows) or otherwise incomparable.
        raise PathSafetyError(f'非法路径: {target_path}')
    if common != base_nc:
        raise PathSafetyError(f'路径越界: {target_path}')
    return target_real
