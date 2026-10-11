"""Validate bytes, never trust client MIME types, and never use names as paths."""
import io
import re
import stat
import warnings
import zipfile

from defusedxml import ElementTree
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import DictionaryObject, ArrayObject, IndirectObject
from werkzeug.utils import secure_filename

from template_content import TemplateValidationError

MAX_FILE_SIZE = 5 * 1024 * 1024
MIME_TYPES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg',
              'jpeg': 'image/jpeg', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'txt': 'text/plain'}


def validate_pdf(data):
    if not data.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF signature')
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or not reader.pages:
        raise ValueError('Encrypted or empty PDF')
    forbidden = {'/JS', '/JavaScript', '/AA', '/OpenAction', '/Launch', '/EmbeddedFiles',
                 '/AcroForm', '/RichMedia', '/XFA', '/SubmitForm', '/GoToR', '/URI', '/AF'}
    visited = set()
    count = 0
    def walk(obj, depth=0):
        nonlocal count
        count += 1
        if count > 20000 or depth > 50:
            raise ValueError('PDF too complex')
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in visited:
                return
            visited.add(key)
            obj = obj.get_object()
        if isinstance(obj, DictionaryObject):
            if forbidden.intersection(obj):
                raise ValueError('Active PDF content is not permitted')
            for value in obj.values():
                walk(value, depth + 1)
        elif isinstance(obj, ArrayObject):
            for value in obj:
                walk(value, depth + 1)
    walk(reader.trailer)


def validate_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(set(names)) or len(names) > 200:
            raise ValueError('Invalid DOCX archive')
        if not {'[Content_Types].xml', 'word/document.xml', '_rels/.rels'}.issubset(names):
            raise ValueError('Missing DOCX structure')
        if sum(item.file_size for item in entries) > 20 * 1024 * 1024:
            raise ValueError('DOCX expansion limit exceeded')
        for item in entries:
            name = item.filename
            if name.startswith(('/', '\\')) or '\\' in name or '..' in name.split('/') or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Unsafe DOCX archive path')
            if item.flag_bits & 1:
                raise ValueError('Encrypted DOCX')
            if name.endswith('/'):
                continue
            # Constrain package parts; excludes macros, embedded binaries, and external data.
            if not name.endswith(('.xml', '.rels')):
                if name.startswith('word/media/') and name.rsplit('.', 1)[-1].lower() in ('png', 'jpg', 'jpeg'):
                    validate_image(archive.read(item), name.rsplit('.', 1)[-1].lower())
                    continue
                raise ValueError('Unsupported DOCX embedded content')
            raw = archive.read(item)
            root = ElementTree.fromstring(raw)
            for node in root.iter():
                tag = node.tag.split('}')[-1]
                if tag in ('object', 'altChunk', 'fldSimple', 'instrText', 'OLEObject'):
                    raise ValueError('Active DOCX content')
                if node.attrib.get('TargetMode', '').lower() == 'external':
                    raise ValueError('External DOCX relationship')
                if tag == 'Override' and ('macro' in node.attrib.get('ContentType', '').lower()):
                    raise ValueError('DOCX macros')
            if name == 'word/document.xml' and root.tag != '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document':
                raise ValueError('Invalid Word document')


def validate_image(data, extension):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        image = Image.open(io.BytesIO(data))
        expected = 'PNG' if extension == 'png' else 'JPEG'
        if image.format != expected or image.width * image.height > 16_000_000:
            raise ValueError('Invalid or oversized image dimensions')
        image.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
        clean = image.convert('RGBA' if expected == 'PNG' else 'RGB')
        output = io.BytesIO()
        clean.save(output, format=expected)
        return output.getvalue()


def validate_upload(upload, kind='attachment'):
    original = upload.filename or ''
    if '/' in original or '\\' in original or ':' in original or '..' in original or any(ord(c) < 32 or ord(c) == 127 for c in original):
        raise TemplateValidationError('Unsafe filename: paths, traversal, and control characters are not allowed.')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{number}' for prefix in ('COM', 'LPT') for number in range(1, 10)}
    if original.split('.', 1)[0].upper() in reserved:
        raise TemplateValidationError('Reserved device filenames are not allowed.')
    filename = secure_filename(original)
    if not filename or '.' not in filename or len(filename) > 180:
        raise TemplateValidationError('File must have a safe filename and supported extension.')
    extension = filename.rsplit('.', 1)[-1].lower()
    if extension not in MIME_TYPES or (kind == 'image' and extension not in ('png', 'jpg', 'jpeg')):
        raise TemplateValidationError('Allowed files: PDF, PNG, JPG/JPEG, DOCX, TXT; illustrations must be PNG or JPEG.')
    data = upload.stream.read(MAX_FILE_SIZE + 1)
    if not data or len(data) > MAX_FILE_SIZE:
        raise TemplateValidationError('Each file must be nonempty and no larger than 5 MB.')
    try:
        if extension in ('png', 'jpg', 'jpeg'):
            data = validate_image(data, extension)
        elif extension == 'pdf':
            validate_pdf(data)
        elif extension == 'docx':
            validate_docx(data)
        else:
            text = data.decode('utf-8-sig')
            if any(ord(c) < 32 and c not in '\t\r\n' for c in text) or re.search(r'<\s*(?:script|form|iframe|html)\b', text, re.I):
                raise ValueError('TXT must be plain UTF-8 text')
    except Exception as exc:
        raise TemplateValidationError('Invalid file or unsafe active content. Upload a passive document or image.') from exc
    if len(data) > MAX_FILE_SIZE:
        raise TemplateValidationError('Processed image exceeds 5 MB.')
    return {'filename': filename, 'mime_type': MIME_TYPES[extension], 'kind': kind, 'data': data}
