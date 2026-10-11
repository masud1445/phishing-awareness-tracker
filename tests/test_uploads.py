import io
import zipfile
import warnings

import pytest
from PIL import Image
from pypdf import PdfWriter
from werkzeug.datastructures import FileStorage

from template_content import TemplateValidationError
from uploads import MAX_FILE_SIZE, validate_upload


def upload(name, data, content_type='application/octet-stream'):
    return FileStorage(stream=io.BytesIO(data), filename=name, content_type=content_type)


def image_bytes(fmt='PNG'):
    data = io.BytesIO()
    Image.new('RGB', (5, 5), 'teal').save(data, format=fmt)
    return data.getvalue()


def pdf_bytes(active=False):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if active:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            writer.add_js('app.alert("unsafe")')
    data = io.BytesIO()
    writer.write(data)
    return data.getvalue()


def docx_bytes(extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Awareness</w:t></w:r></w:p></w:body></w:document>')
        if extra:
            archive.writestr(*extra)
    return data.getvalue()


@pytest.mark.parametrize('name,data,mime', [
    ('guide.pdf', pdf_bytes(), 'application/pdf'), ('illustration.png', image_bytes(), 'image/png'),
    ('image.jpg', image_bytes('JPEG'), 'image/jpeg'), ('image.JPEG', image_bytes('JPEG'), 'image/jpeg'),
    ('guide.docx', docx_bytes(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    ('note.txt', b'Safe awareness guidance', 'text/plain'),
])
def test_allowed_types_validated_by_content(name, data, mime):
    asset = validate_upload(upload(name, data, 'fake/client-mime'))
    assert asset['mime_type'] == mime
    assert asset['data']


@pytest.mark.parametrize('name', ['script.py', 'script.js', 'run.exe', 'macro.docm', 'archive.zip', 'legacy.doc', 'vector.svg', 'page.html', '../guide.txt', 'dir/guide.txt', 'C:\\guide.txt', '.hidden', 'bad\x00.txt', 'bad\n.txt', '...txt', 'CON.txt', 'NUL.txt', 'COM1.txt', 'file:stream.txt'])
def test_unsafe_extensions_and_filenames_rejected(name):
    with pytest.raises(TemplateValidationError):
        validate_upload(upload(name, b'safe'))


@pytest.mark.parametrize('name,data', [
    ('fake.png', b'not an image'), ('fake.pdf', b'not a pdf'), ('fake.docx', b'not a zip'),
    ('binary.txt', b'\x00\xff'), ('script.txt', b'<script>alert(1)</script>'),
    ('active.pdf', pdf_bytes(True)), ('macro.docx', docx_bytes(('word/vbaProject.bin', b'macro'))),
    ('traversal.docx', docx_bytes(('../outside.txt', b'safe'))),
    ('external.docx', docx_bytes(('word/_rels/document.xml.rels', '<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'))),
    ('entity.docx', docx_bytes(('word/styles.xml', '<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>'))),
])
def test_disguised_or_active_content_rejected(name, data):
    with pytest.raises(TemplateValidationError):
        validate_upload(upload(name, data))


def test_limit_exactly_five_mb_and_over():
    assert len(validate_upload(upload('note.txt', b'x' * MAX_FILE_SIZE))['data']) == MAX_FILE_SIZE
    with pytest.raises(TemplateValidationError):
        validate_upload(upload('note.txt', b'x' * (MAX_FILE_SIZE + 1)))
    with pytest.raises(TemplateValidationError):
        validate_upload(upload('note.txt', b''))


def test_image_slot_rejects_documents():
    with pytest.raises(TemplateValidationError):
        validate_upload(upload('note.txt', b'awareness'), 'image')


def test_images_reencoded_to_remove_trailing_payload():
    data = image_bytes() + b'<script>payload</script>'
    result = validate_upload(upload('image.png', data), 'image')
    assert b'<script>' not in result['data']


def test_secure_filename_normalization():
    assert validate_upload(upload('Awareness guide (1).txt', b'guide'))['filename'] == 'Awareness_guide_1.txt'
