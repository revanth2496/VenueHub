import os, secrets
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash

ROOT=os.path.abspath(os.path.dirname(__file__))
DATABASE_URL=os.environ.get('DATABASE_URL', '').strip()
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
if DATABASE_URL and 'sslmode=' not in DATABASE_URL:
    DATABASE_URL += ('&' if '%s' in DATABASE_URL else '%s') + 'sslmode=require'
app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError('SECRET_KEY environment variable is required. Set it in Render.')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('RENDER', '').lower() == 'true')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')

class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

def hybrid_row_factory(cursor):
    columns = [column.name for column in cursor.description]
    return lambda values: HybridRow(zip(columns, values))

def conn():
    if 'db' not in g:
        if not DATABASE_URL:
            raise RuntimeError('DATABASE_URL is not configured. Add your Neon PostgreSQL connection string.')
        g.db = psycopg.connect(DATABASE_URL, row_factory=hybrid_row_factory)
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    d = g.pop('db', None)
    if d:
        if e is not None:
            d.rollback()
        d.close()

def init_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL is not configured. Add your Neon PostgreSQL connection string.')
    with psycopg.connect(DATABASE_URL) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_uq ON users(LOWER(email))")
        c.execute("""
            CREATE TABLE IF NOT EXISTS halls(
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL,
                hourly_rate DOUBLE PRECISION NOT NULL,
                description TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS bookings(
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(id),
                customer_name TEXT NOT NULL DEFAULT '',
                customer_email TEXT NOT NULL DEFAULT '',
                customer_phone TEXT NOT NULL DEFAULT '',
                hall_id BIGINT NOT NULL REFERENCES halls(id),
                event_name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                guests INTEGER NOT NULL,
                notes TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS maintenance(
                id BIGSERIAL PRIMARY KEY,
                hall_id BIGINT NOT NULL REFERENCES halls(id),
                maintenance_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")

        admin_count = c.execute("SELECT COUNT(*) AS total FROM users WHERE role='admin'").fetchone()['total']
        if admin_count == 0:
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@venuehub.local').strip().lower()
            admin_password = os.environ.get('ADMIN_PASSWORD', '')
            admin_name = os.environ.get('ADMIN_NAME', 'VenueHub Administrator').strip()
            if len(admin_password) < 12:
                raise RuntimeError('Set ADMIN_PASSWORD in Render (at least 12 characters) before first deployment.')
            c.execute(
                "INSERT INTO users(name,email,password_hash,role,created_at) VALUES(%s,%s,%s,'admin',%s)",
                (admin_name, admin_email, generate_password_hash(admin_password),
                 datetime.now().strftime('%Y-%m-%d %H:%M'))
            )

        default_settings = {
            'venue_name': 'VenueHub',
            'contact_email': os.environ.get('ADMIN_EMAIL', 'admin@venuehub.local'),
            'contact_phone': '',
            'venue_address': '',
            'venue_description': 'Community Hall Booking and Management System'
        }
        for key, value in default_settings.items():
            c.execute(
                'INSERT INTO app_settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO NOTHING',
                (key, value)
            )

        hall_count = c.execute('SELECT COUNT(*) AS total FROM halls').fetchone()['total']
        if hall_count == 0:
            c.cursor().executemany(
                'INSERT INTO halls(name,capacity,hourly_rate,description) VALUES(%s,%s,%s,%s)',
                [
                    ('Grand Community Hall', 300, 1500, 'Large venue for weddings and receptions.'),
                    ('Sunrise Function Hall', 150, 900, 'A welcoming space for family functions.'),
                    ('Harmony Meeting Room', 60, 500, 'A compact venue for workshops and meetings.')
                ]
            )

def admin_required(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get('admin_uid'):
            return redirect(url_for('login'))
        u=conn().execute("SELECT * FROM users WHERE id=%s AND role='admin' AND active=1",(session['admin_uid'],)).fetchone()
        if not u:
            session.clear(); return redirect(url_for('login'))
        g.user=u
        return f(*a,**kw)
    return w
def get_settings():
    return {row['key']: row['value'] for row in conn().execute('SELECT key,value FROM app_settings').fetchall()}

@app.context_processor
def inject():
    u=conn().execute("SELECT id,name,email,role FROM users WHERE id=%s AND role='admin'",(session.get('admin_uid'),)).fetchone() if session.get('admin_uid') else None
    return {'current_user':u,'today':date.today().isoformat(),'site_settings':get_settings()}
def overlap(a,b,c,d): return a<d and c<b
def conflict(db,hid,day,start,end,exclude=None):
    rows=db.execute("SELECT id,start_time,end_time FROM bookings WHERE hall_id=%s AND event_date=%s AND status IN ('Pending','Approved')",(hid,day)).fetchall()
    for r in rows:
        if r['id']!=exclude and overlap(start,end,r['start_time'],r['end_time']): return 'That time slot already has a pending or approved booking.'
    rows=db.execute('SELECT start_time,end_time FROM maintenance WHERE hall_id=%s AND maintenance_date=%s',(hid,day)).fetchall()
    if any(overlap(start,end,r['start_time'],r['end_time']) for r in rows): return 'That time slot is blocked for maintenance.'
    return None
@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=conn().execute("SELECT * FROM users WHERE email=%s AND role='admin'",(request.form.get('email','').strip().lower(),)).fetchone()
        if not u or not check_password_hash(u['password_hash'],request.form.get('password','')) or not u['active']:
            flash('Invalid admin login.','error')
        else:
            session.clear(); session['admin_uid']=u['id']; flash('Welcome, '+u['name']+'!','success'); return redirect(url_for('admin_dashboard'))
    return render_template('login.html')

@app.post('/logout')
def logout():
    session.clear(); flash('Admin logged out.','success'); return redirect(url_for('index'))

@app.route('/halls')
def halls():
    return render_template('halls.html',halls=conn().execute('SELECT * FROM halls WHERE active=1 ORDER BY name').fetchall())

@app.route('/book/<int:hid>',methods=['GET','POST'])
def book(hid):
    d=conn(); hall=d.execute('SELECT * FROM halls WHERE id=%s AND active=1',(hid,)).fetchone()
    if not hall:
        flash('Hall is not available.','error'); return redirect(url_for('halls'))
    if request.method=='POST':
        customer_name=request.form.get('customer_name','').strip()
        customer_email=request.form.get('customer_email','').strip().lower()
        customer_phone=request.form.get('customer_phone','').strip()
        event=request.form.get('event_name','').strip(); day=request.form.get('event_date','')
        start=request.form.get('start_time',''); end=request.form.get('end_time','')
        guests=request.form.get('guests',type=int); notes=request.form.get('notes','').strip()
        if not all([customer_name,customer_email,customer_phone,event,day,start,end,guests]):
            flash('Please complete all required fields.','error')
        elif day<date.today().isoformat() or start>=end:
            flash('Choose a valid future date and time range.','error')
        elif guests<1 or guests>hall['capacity']:
            flash(f"Guest count must be between 1 and {hall['capacity']}.",'error')
        else:
            problem=conflict(d,hid,day,start,end)
            if problem: flash(problem,'error')
            else:
                d.execute("INSERT INTO bookings(user_id,customer_name,customer_email,customer_phone,hall_id,event_name,event_date,start_time,end_time,guests,notes,status,created_at) VALUES(NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Pending',%s)",(customer_name,customer_email,customer_phone,hid,event,day,start,end,guests,notes,datetime.now().strftime('%Y-%m-%d %H:%M')))
                d.commit(); flash('Your booking request has been submitted. The admin will contact you.','success'); return redirect(url_for('booking_success'))
    return render_template('book.html',hall=hall)

@app.route('/booking-success')
def booking_success():
    return render_template('booking_success.html')

@app.route('/admin')
@admin_required
def admin_dashboard():
    d=conn(); stats={'users':d.execute('SELECT COUNT(DISTINCT customer_email) FROM bookings').fetchone()[0],'halls':d.execute('SELECT COUNT(*) FROM halls WHERE active=1').fetchone()[0],'total':d.execute('SELECT COUNT(*) FROM bookings').fetchone()[0],'pending':d.execute("SELECT COUNT(*) FROM bookings WHERE status='Pending'").fetchone()[0],'approved':d.execute("SELECT COUNT(*) FROM bookings WHERE status='Approved'").fetchone()[0]}
    stats['revenue']=d.execute("SELECT COALESCE(SUM((((CAST(SPLIT_PART(b.end_time,':',1) AS DOUBLE PRECISION)*60 + CAST(SPLIT_PART(b.end_time,':',2) AS DOUBLE PRECISION)) - (CAST(SPLIT_PART(b.start_time,':',1) AS DOUBLE PRECISION)*60 + CAST(SPLIT_PART(b.start_time,':',2) AS DOUBLE PRECISION))) / 60.0) * h.hourly_rate),0) AS revenue FROM bookings b JOIN halls h ON h.id=b.hall_id WHERE b.status='Approved'").fetchone()[0]
    recent=d.execute('SELECT b.*,h.name hall_name FROM bookings b JOIN halls h ON h.id=b.hall_id ORDER BY b.id DESC LIMIT 8').fetchall()
    return render_template('admin_dashboard.html',stats=stats,recent=recent)
@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    status=request.args.get('status',''); d=conn(); sql='SELECT b.*,h.name hall_name FROM bookings b JOIN halls h ON h.id=b.hall_id'; args=()
    if status in ('Pending','Approved','Rejected','Cancelled'): sql+=' WHERE b.status=%s'; args=(status,)
    sql+=' ORDER BY b.event_date,b.start_time'
    return render_template('admin_bookings.html',bookings=d.execute(sql,args).fetchall(),current_status=status)
@app.post('/admin/booking/<int:bid>/status')
@admin_required
def admin_booking_status(bid):
    status=request.form.get('status'); d=conn(); b=d.execute('SELECT * FROM bookings WHERE id=%s',(bid,)).fetchone()
    if status not in ('Pending','Approved','Rejected','Cancelled'): flash('Invalid status.','error')
    elif not b: flash('Booking not found.','error')
    elif status=='Approved' and conflict(d,b['hall_id'],b['event_date'],b['start_time'],b['end_time'],bid): flash(conflict(d,b['hall_id'],b['event_date'],b['start_time'],b['end_time'],bid)+' Not approved.','error')
    else: d.execute('UPDATE bookings SET status=%s WHERE id=%s',(status,bid)); d.commit(); flash('Booking status updated.','success')
    return redirect(url_for('admin_bookings'))
@app.route('/admin/halls',methods=['GET','POST'])
@admin_required
def admin_halls():
    d=conn()
    if request.method=='POST':
        name=request.form.get('name','').strip(); cap=request.form.get('capacity',type=int); rate=request.form.get('hourly_rate',type=float); desc=request.form.get('description','').strip()
        if not name or not cap or cap<1 or rate is None or rate<0: flash('Enter valid hall details.','error')
        else: d.execute('INSERT INTO halls(name,capacity,hourly_rate,description) VALUES(%s,%s,%s,%s)',(name,cap,rate,desc)); d.commit(); flash('Hall added.','success'); return redirect(url_for('admin_halls'))
    return render_template('admin_halls.html',halls=d.execute('SELECT * FROM halls ORDER BY active DESC,name').fetchall())
@app.route('/admin/hall/<int:hid>/edit',methods=['GET','POST'])
@admin_required
def edit_hall(hid):
    d=conn(); h=d.execute('SELECT * FROM halls WHERE id=%s',(hid,)).fetchone()
    if not h: flash('Hall not found.','error'); return redirect(url_for('admin_halls'))
    if request.method=='POST':
        name=request.form.get('name','').strip(); cap=request.form.get('capacity',type=int); rate=request.form.get('hourly_rate',type=float); desc=request.form.get('description','').strip()
        if not name or not cap or cap<1 or rate is None or rate<0: flash('Enter valid hall details.','error')
        else: d.execute('UPDATE halls SET name=%s,capacity=%s,hourly_rate=%s,description=%s WHERE id=%s',(name,cap,rate,desc,hid)); d.commit(); flash('Hall updated.','success'); return redirect(url_for('admin_halls'))
    return render_template('edit_hall.html',hall=h)
@app.post('/admin/hall/<int:hid>/toggle')
@admin_required
def toggle_hall(hid):
    d=conn(); h=d.execute('SELECT * FROM halls WHERE id=%s',(hid,)).fetchone()
    if h: d.execute('UPDATE halls SET active=%s WHERE id=%s',(0 if h['active'] else 1,hid)); d.commit(); flash('Hall availability updated.','success')
    return redirect(url_for('admin_halls'))
@app.route('/admin/maintenance',methods=['GET','POST'])
@admin_required
def admin_maintenance():
    d=conn()
    if request.method=='POST':
        hid=request.form.get('hall_id',type=int); day=request.form.get('maintenance_date',''); start=request.form.get('start_time',''); end=request.form.get('end_time',''); note=request.form.get('note','').strip()
        if not all([hid,day,start,end,note]): flash('Complete every field.','error')
        elif day<date.today().isoformat() or start>=end: flash('Choose a valid future date and time range.','error')
        else:
            problem=conflict(d,hid,day,start,end)
            if problem: flash(problem,'error')
            else: d.execute('INSERT INTO maintenance(hall_id,maintenance_date,start_time,end_time,note,created_at) VALUES(%s,%s,%s,%s,%s,%s)',(hid,day,start,end,note,datetime.now().strftime('%Y-%m-%d %H:%M'))); d.commit(); flash('Maintenance scheduled.','success'); return redirect(url_for('admin_maintenance'))
    return render_template('admin_maintenance.html',halls=d.execute('SELECT * FROM halls ORDER BY name').fetchall(),entries=d.execute('SELECT m.*,h.name hall_name FROM maintenance m JOIN halls h ON h.id=m.hall_id ORDER BY m.maintenance_date,m.start_time').fetchall())
@app.post('/admin/maintenance/<int:mid>/delete')
@admin_required
def delete_maintenance(mid): conn().execute('DELETE FROM maintenance WHERE id=%s',(mid,)); conn().commit(); flash('Maintenance removed.','success'); return redirect(url_for('admin_maintenance'))
@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    d = conn()
    admin = d.execute("SELECT * FROM users WHERE id=%s AND role='admin'", (session['admin_uid'],)).fetchone()
    settings = get_settings()
    if request.method == 'POST':
        action = request.form.get('action', 'profile')
        if action == 'profile':
            name = request.form.get('admin_name', '').strip()
            email = request.form.get('admin_email', '').strip().lower()
            current_password = request.form.get('current_password', '')
            new_password = request.form.get('new_password', '')
            confirm_password = request.form.get('confirm_password', '')
            if not name or not email or '@' not in email:
                flash('Enter a valid admin name and email address.', 'error')
            elif d.execute('SELECT id FROM users WHERE LOWER(email)=LOWER(%s) AND id<>%s', (email, admin['id'])).fetchone():
                flash('That email address is already in use.', 'error')
            elif new_password and not check_password_hash(admin['password_hash'], current_password):
                flash('Enter your current password to change the password.', 'error')
            elif new_password and (len(new_password) < 8 or new_password != confirm_password):
                flash('New passwords must match and contain at least 8 characters.', 'error')
            else:
                if new_password:
                    d.execute('UPDATE users SET name=%s,email=%s,password_hash=%s WHERE id=%s', (name,email,generate_password_hash(new_password),admin['id']))
                else:
                    d.execute('UPDATE users SET name=%s,email=%s WHERE id=%s', (name,email,admin['id']))
                d.commit(); flash('Admin account details updated successfully.', 'success')
                return redirect(url_for('admin_settings'))
        elif action == 'venue':
            values = {'venue_name':request.form.get('venue_name','').strip(),'contact_email':request.form.get('contact_email','').strip(),'contact_phone':request.form.get('contact_phone','').strip(),'venue_address':request.form.get('venue_address','').strip(),'venue_description':request.form.get('venue_description','').strip()}
            if not values['venue_name']:
                flash('Venue / website name cannot be empty.', 'error')
            elif values['contact_email'] and '@' not in values['contact_email']:
                flash('Enter a valid contact email address.', 'error')
            else:
                d.cursor().executemany('INSERT INTO app_settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value', list(values.items()))
                d.commit(); flash('Venue details updated successfully.', 'success')
                return redirect(url_for('admin_settings'))
        admin = d.execute("SELECT * FROM users WHERE id=%s AND role='admin'", (session['admin_uid'],)).fetchone()
        settings = get_settings()
    return render_template('admin_settings.html', admin=admin, settings=settings)

@app.route('/admin/reports')
@admin_required
def reports():
    d=conn()
    statuses=d.execute('SELECT status, COUNT(*) AS total FROM bookings GROUP BY status ORDER BY status').fetchall()
    hall_rows=[]
    hall_list=d.execute('SELECT id,name,hourly_rate FROM halls ORDER BY name').fetchall()
    for hall in hall_list:
        rows=d.execute("SELECT start_time,end_time,status FROM bookings WHERE hall_id=%s",(hall['id'],)).fetchall()
        revenue=0.0
        for row in rows:
            if row['status']=='Approved':
                sh,sm=map(int,row['start_time'].split(':')); eh,em=map(int,row['end_time'].split(':'))
                revenue += ((eh*60+em)-(sh*60+sm))/60 * hall['hourly_rate']
        hall_rows.append({'name':hall['name'],'bookings':len(rows),'revenue':revenue})
    monthly=d.execute("SELECT substr(event_date,1,7) AS month, COUNT(*) AS total FROM bookings GROUP BY substr(event_date,1,7) ORDER BY month DESC LIMIT 12").fetchall()
    return render_template('reports.html',status_rows=statuses,hall_rows=hall_rows,monthly=monthly)
# Render/Gunicorn imports this module, so initialize the Neon schema on startup.
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
