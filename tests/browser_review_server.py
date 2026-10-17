"""Isolated automated browser fixture; never accesses the application database."""
import sys,os,json
from pathlib import Path
from contextlib import contextmanager
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
os.environ['SMTP_FROM_EMAIL']='review@example.test'
os.environ['SMTP_ALLOWED_FROM_EMAILS']=''
os.environ['SMTP_HOST']='smtp.example.test'
os.environ['SMTP_USER']='review@example.test'
os.environ['SMTP_PASSWORD']='isolated-ui-fixture-not-a-real-secret'
os.environ['BASE_URL']='http://127.0.0.1:5056'
from werkzeug.security import generate_password_hash
os.environ["APP_ENV"]="local"
os.environ["ADMIN_USERNAME"]="review-operator"
os.environ["ADMIN_PASSWORD_HASH"]=generate_password_hash("isolated-browser-review-password")
from cryptography.fernet import Fernet
os.environ['SENDER_ENCRYPTION_KEY']=Fernet.generate_key().decode()
import app as app_module,studio,template_store,sender_accounts,email_service
from sqlite_adapter import SQLiteConnection
from template_content import validate_template
from tempfile import TemporaryDirectory
fixture_directory=TemporaryDirectory(prefix='awareness-ui-review-')
path=Path(fixture_directory.name)/'fixture.sqlite3'
print('Isolated UI fixture database:',path,flush=True)
factory=lambda:SQLiteConnection(path)
connection=factory();connection.connection.executescript((ROOT/'tests/sqlite_schema.sql').read_text());connection.close()
for module in [app_module,studio,template_store,sender_accounts]:module.get_db_connection=factory
@contextmanager
def test_transport(settings):
    class SMTP:
        def mail(self,email):return 250,b'Fixture accepted'
        def rset(self):pass
    yield SMTP()
sender_accounts.open_account_connection=test_transport
sender_accounts.synchronize_configured()
identity=sender_accounts.list_accounts()[0]['id'];sender_accounts.verify_account(identity);sender_accounts.account_action(identity,'default')
@app_module.app.after_request
def review(response):
 response.headers['X-Review-Email-Delivery']='disabled';response.headers['X-Review-Data']='isolated-automated-fixture';return response

sender_accounts.save_account({'display_name':'UI review · Training team','email':'training@example.test','provider':'gmail','smtp_host':'smtp.gmail.com','smtp_port':587,'tls_mode':'starttls','smtp_username':'training@example.test'},'isolated-ui-fixture-not-a-real-secret')
client=app_module.app.test_client()
client.get('/operator/login')
with client.session_transaction() as session: token=session['studio_csrf']
assert client.post('/operator/login',data={'csrf_token':token,'username':'review-operator','password':'isolated-browser-review-password'}).status_code==302
def post(url,data=None):
    with client.session_transaction() as session: token=session['studio_csrf']
    response=client.post(url,data={**(data or {}),'csrf_token':token})
    assert response.status_code==302, (url,response.status_code)
    return response
for name,kind in [('UI review · Finance awareness','document-share'),('UI review · Security essentials','security-notice'),('UI review · New team onboarding','basic-link')]:
    post('/campaigns/new',data={'campaign_name':name,'purpose':'Isolated automated visual-review fixture','campaign_template':kind,'start_date':'2026-10-12','authorized':'on'})
for campaign_id in [1,2]:
    for name,department in [('Alex Morgan','Finance'),('Sam Taylor','Operations'),('Jordan Lee','People')]:
        response=post(f'/campaigns/{campaign_id}/recipients/new',data={'full_name':name,'email':name.lower().replace(' ','.')+'@example.test','department':department})
connection=factory()
with connection.cursor() as cursor:
    cursor.execute('UPDATE recipients SET approved=1 WHERE id IN (1,2,3,4,5)')
    cursor.execute("INSERT INTO tracking_events (campaign_id,recipient_id,event_type) VALUES (1,1,'link_clicked')")
    cursor.execute("UPDATE recipients SET status='clicked' WHERE id=1")
with connection.cursor() as cursor:
    cursor.execute('SELECT tracking_token FROM recipients WHERE id=1')
    Path('/tmp/security-ui-token').write_text(cursor.fetchone()['tracking_token'])
connection.close()
values={'name':'Team security reminder','subject':'A quick awareness check for {{campaign_name}}','body':'## A moment to reflect\nHello {{recipient_name}},\n\nTake a moment to review this authorized awareness exercise.\n---\nUse the button below to see the information.','sender_name':'Security Awareness Team','sender_email':'review@example.test','sender_account_id':identity,'reference_link':'https://example.org/awareness','button_label':'Review campaign information','email_heading':'Small decisions. Stronger security.','content_style':'sectioned','text_alignment':'left'}
from io import BytesIO
from PIL import Image
from werkzeug.datastructures import FileStorage
from uploads import validate_upload
image=BytesIO();Image.new('RGB',(24,24),'blue').save(image,format='PNG');image.seek(0)
asset=validate_upload(FileStorage(stream=image,filename='review-image.png'),'image')
id=template_store.save_template(validate_template(values),[asset]);template_store.apply_template(1,template_store.get_template(id));post('/campaigns/1/launch')
values=values|{'name':'New team welcome','subject':'Welcome to security awareness','content_style':'standard'}
template_store.save_template(validate_template(values),[])

def block(*args,**kwargs):raise email_service.EmailDeliveryError('Real email delivery is disabled during manual UI review.')
app_module.send_campaign_email=block;email_service.send_campaign_email=block
app_module.app.run(host='127.0.0.1',port=5056,debug=False,use_reloader=False)
