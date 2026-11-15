import logging
import os
import secrets
from datetime import date, datetime
from functools import wraps

from flask import Flask, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
IN_PRODUKTION = bool(os.environ.get('FLY_APP_NAME'))

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    if IN_PRODUKTION:
        logging.warning('SECRET_KEY ist nicht gesetzt – es wird ein zufälliger Schlüssel verwendet '
                        '(Admin-Logins gehen bei jedem Neustart verloren). '
                        'Bitte setzen: fly secrets set SECRET_KEY=...')
        _secret_key = secrets.token_hex(32)
    else:
        _secret_key = 'nur-fuer-lokale-entwicklung'
app.config['SECRET_KEY'] = _secret_key
_db_path = os.environ.get('DATABASE_PATH', '')
if _db_path:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_db_path}'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tennis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# In production the admin password must come from the environment; the
# well-known development default is only accepted when running locally.
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or (None if IN_PRODUKTION else 'tennis123')
if ADMIN_PASSWORD is None:
    logging.warning('ADMIN_PASSWORD ist nicht gesetzt – Admin-Login ist deaktiviert. '
                    'Bitte setzen: fly secrets set ADMIN_PASSWORD=...')


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Einstellung(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)


class Spieltermin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    datum = db.Column(db.String(10), nullable=False, unique=True)  # YYYY-MM-DD
    verfuegbarkeiten = db.relationship('Verfuegbarkeit', backref='termin', cascade='all, delete-orphan')
    aufstellungen = db.relationship('Aufstellung', backref='termin', cascade='all, delete-orphan')
    spiele = db.relationship('Spiel', backref='termin', cascade='all, delete-orphan', order_by='Spiel.mannschaft')

    @property
    def datum_anzeige(self):
        try:
            return datetime.strptime(self.datum, '%Y-%m-%d').strftime('%d.%m.%Y')
        except ValueError:
            return self.datum


class Kind(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    verfuegbarkeiten = db.relationship('Verfuegbarkeit', backref='kind', cascade='all, delete-orphan')
    aufstellungen = db.relationship('Aufstellung', backref='kind', cascade='all, delete-orphan')


class Verfuegbarkeit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind_id = db.Column(db.Integer, db.ForeignKey('kind.id'), nullable=False)
    termin_id = db.Column(db.Integer, db.ForeignKey('spieltermin.id'), nullable=False)
    verfuegbar = db.Column(db.Boolean, default=False)
    __table_args__ = (db.UniqueConstraint('kind_id', 'termin_id'),)


class Aufstellung(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    termin_id = db.Column(db.Integer, db.ForeignKey('spieltermin.id'), nullable=False)
    kind_id = db.Column(db.Integer, db.ForeignKey('kind.id'), nullable=False)
    mannschaft = db.Column(db.String(10), nullable=True)   # "BD 1" | "BD 2"
    rolle = db.Column(db.String(10), nullable=False)       # "spieler" | "ersatz"
    position = db.Column(db.Integer, nullable=False)       # 1-based ordering


class Spiel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    termin_id = db.Column(db.Integer, db.ForeignKey('spieltermin.id'), nullable=False)
    mannschaft = db.Column(db.String(10), nullable=False)   # "BD 1" | "BD 2"
    uhrzeit = db.Column(db.String(5), nullable=False)       # "11:00"
    gegner = db.Column(db.String(200), nullable=False)
    heimspiel = db.Column(db.Boolean, nullable=False)
    maps_link = db.Column(db.String(500), nullable=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ist_gesperrt():
    e = db.session.get(Einstellung, 'gesperrt')
    return e is not None and e.value == '1'


def ist_admin():
    return session.get('admin') is True


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ist_admin():
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


def normalisiere_name(name):
    """Collapse whitespace so 'Max  Muster ' and 'Max Muster' are the same."""
    return ' '.join((name or '').split())


def finde_kind(name):
    """Case-insensitive lookup (in Python, so umlauts compare correctly too)."""
    key = normalisiere_name(name).casefold()
    if not key:
        return None
    for kind in Kind.query.all():
        if normalisiere_name(kind.name).casefold() == key:
            return kind
    return None


def get_kind_name():
    return normalisiere_name(request.cookies.get('kind_name', ''))


# ---------------------------------------------------------------------------
# Parent routes
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = normalisiere_name(request.form.get('name', ''))
        if name:
            vorhanden = finde_kind(name)
            if vorhanden:
                name = vorhanden.name
            resp = make_response(redirect(url_for('verfuegbarkeit')))
            resp.set_cookie('kind_name', name, max_age=30 * 24 * 3600)
            return resp
    kind_name = get_kind_name()
    return render_template('index.html', kind_name=kind_name)


@app.route('/verfuegbarkeit', methods=['GET', 'POST'])
def verfuegbarkeit():
    kind_name = get_kind_name()
    if not kind_name:
        return redirect(url_for('index'))

    gesperrt = ist_gesperrt()
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()

    if request.method == 'POST' and not gesperrt:
        kind = finde_kind(kind_name)
        if not kind:
            kind = Kind(name=kind_name)
            db.session.add(kind)
            db.session.flush()

        for termin in termine:
            verfuegbar = request.form.get(f'termin_{termin.id}') == 'on'
            v = Verfuegbarkeit.query.filter_by(kind_id=kind.id, termin_id=termin.id).first()
            if v:
                v.verfuegbar = verfuegbar
            else:
                db.session.add(Verfuegbarkeit(kind_id=kind.id, termin_id=termin.id, verfuegbar=verfuegbar))
        db.session.commit()
        return redirect(url_for('verfuegbarkeit'))

    kind = finde_kind(kind_name)
    verfuegbarkeiten = {}
    if kind:
        for v in Verfuegbarkeit.query.filter_by(kind_id=kind.id).all():
            verfuegbarkeiten[v.termin_id] = v.verfuegbar

    return render_template('verfuegbarkeit.html',
                           termine=termine,
                           verfuegbarkeiten=verfuegbarkeiten,
                           gesperrt=gesperrt,
                           kind_name=kind_name)


@app.route('/statistik')
def statistik():
    kinder = Kind.query.order_by(Kind.name).all()
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    total_termine = len(termine)

    from collections import Counter
    bd1_c, bd2_c, ersatz_c = Counter(), Counter(), Counter()
    for a in Aufstellung.query.all():
        if a.rolle == 'ersatz':
            ersatz_c[a.kind_id] += 1
        elif a.mannschaft == 'BD 1':
            bd1_c[a.kind_id] += 1
        elif a.mannschaft == 'BD 2':
            bd2_c[a.kind_id] += 1
    verf_c = Counter(v.kind_id for v in Verfuegbarkeit.query.filter_by(verfuegbar=True).all())

    rows = []
    for kind in kinder:
        bd1, bd2 = bd1_c[kind.id], bd2_c[kind.id]
        ersatz, verfuegbar = ersatz_c[kind.id], verf_c[kind.id]
        rows.append({
            'kind': kind,
            'bd1': bd1,
            'bd2': bd2,
            'gesamt': bd1 + bd2,
            'ersatz': ersatz,
            'verfuegbar': verfuegbar,
        })

    rows.sort(key=lambda r: (-r['gesamt'], r['kind'].name))
    return render_template('statistik.html', rows=rows, total_termine=total_termine)


@app.route('/uebersicht')
def uebersicht():
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    kinder = Kind.query.order_by(Kind.name).all()

    vorhanden = {(v.kind_id, v.termin_id): v.verfuegbar for v in Verfuegbarkeit.query.all()}
    matrix = {kind.id: {t.id: bool(vorhanden.get((kind.id, t.id), False)) for t in termine}
              for kind in kinder}

    return render_template('uebersicht.html',
                           termine=termine,
                           kinder=kinder,
                           matrix=matrix,
                           gesperrt=ist_gesperrt())


@app.route('/aufstellung')
def aufstellung():
    gesperrt = ist_gesperrt()
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()

    kinder = {k.id: k for k in Kind.query.all()}
    lineup = {t.id: {s.mannschaft: {'spieler': [], 'ersatz': []} for s in t.spiele} for t in termine}
    for e in Aufstellung.query.order_by(Aufstellung.position).all():
        ziel = lineup.get(e.termin_id, {}).get(e.mannschaft)
        if ziel is not None and e.rolle in ziel:
            ziel[e.rolle].append((e, kinder.get(e.kind_id)))

    return render_template('aufstellung.html',
                           termine=termine,
                           lineup=lineup,
                           gesperrt=gesperrt,
                           ist_admin=ist_admin())


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    fehler = None
    if request.method == 'POST':
        eingabe = request.form.get('passwort', '')
        if ADMIN_PASSWORD is None:
            fehler = 'Admin-Login ist nicht eingerichtet (ADMIN_PASSWORD fehlt).'
        elif secrets.compare_digest(eingabe.encode(), ADMIN_PASSWORD.encode()):
            session['admin'] = True
            return redirect(url_for('admin'))
        else:
            fehler = 'Falsches Passwort.'
    return render_template('admin_login.html', fehler=fehler)


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('index'))


@app.route('/admin')
@admin_required
def admin():
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    kinder = Kind.query.order_by(Kind.name).all()
    hat_aufstellung = Aufstellung.query.first() is not None
    return render_template('admin.html',
                           termine=termine,
                           kinder=kinder,
                           gesperrt=ist_gesperrt(),
                           hat_aufstellung=hat_aufstellung)


@app.route('/admin/termin/hinzufuegen', methods=['POST'])
@admin_required
def termin_hinzufuegen():
    datum      = request.form.get('datum', '').strip()
    mannschaft = request.form.get('mannschaft', '').strip()
    uhrzeit    = request.form.get('uhrzeit', '').strip()
    gegner     = request.form.get('gegner', '').strip()
    heimspiel  = request.form.get('heimspiel') == '1'
    maps_link  = request.form.get('maps_link', '').strip() or None

    if not (datum and mannschaft and uhrzeit and gegner):
        return redirect(url_for('admin'))

    termin = Spieltermin.query.filter_by(datum=datum).first()
    if not termin:
        termin = Spieltermin(datum=datum)
        db.session.add(termin)
        db.session.flush()

    existing = Spiel.query.filter_by(termin_id=termin.id, mannschaft=mannschaft).first()
    if existing:
        existing.uhrzeit   = uhrzeit
        existing.gegner    = gegner
        existing.heimspiel = heimspiel
        existing.maps_link = maps_link
    else:
        db.session.add(Spiel(termin_id=termin.id, mannschaft=mannschaft,
                             uhrzeit=uhrzeit, gegner=gegner,
                             heimspiel=heimspiel, maps_link=maps_link))
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/spiel/<int:spiel_id>/bearbeiten', methods=['POST'])
@admin_required
def spiel_bearbeiten(spiel_id):
    spiel      = db.get_or_404(Spiel, spiel_id)
    old_termin = spiel.termin
    new_datum  = request.form.get('datum', '').strip()
    new_mann   = request.form.get('mannschaft', spiel.mannschaft).strip()

    if new_datum and (new_datum != old_termin.datum or new_mann != spiel.mannschaft):
        new_termin = Spieltermin.query.filter_by(datum=new_datum).first()
        if not new_termin:
            new_termin = Spieltermin(datum=new_datum)
            db.session.add(new_termin)
            db.session.flush()
        spiel.termin_id  = new_termin.id
        spiel.mannschaft = new_mann
        db.session.flush()
        if not old_termin.spiele:
            db.session.delete(old_termin)

    spiel.uhrzeit   = request.form.get('uhrzeit',  spiel.uhrzeit).strip()
    spiel.gegner    = request.form.get('gegner',   spiel.gegner).strip()
    spiel.heimspiel = request.form.get('heimspiel') == '1'
    spiel.maps_link = request.form.get('maps_link', '').strip() or None
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/spiel/<int:spiel_id>/loeschen', methods=['POST'])
@admin_required
def spiel_loeschen(spiel_id):
    spiel = db.get_or_404(Spiel, spiel_id)
    termin = spiel.termin
    db.session.delete(spiel)
    db.session.flush()
    if not termin.spiele:
        db.session.delete(termin)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/termin/<int:termin_id>/loeschen', methods=['POST'])
@admin_required
def termin_loeschen(termin_id):
    termin = db.get_or_404(Spieltermin, termin_id)
    db.session.delete(termin)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/kind/<int:kind_id>/loeschen', methods=['POST'])
@admin_required
def kind_loeschen(kind_id):
    kind = db.get_or_404(Kind, kind_id)
    db.session.delete(kind)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/sperren', methods=['POST'])
@admin_required
def sperren():
    gesperrt = ist_gesperrt()
    e = db.session.get(Einstellung, 'gesperrt')
    if e:
        e.value = '0' if gesperrt else '1'
    else:
        db.session.add(Einstellung(key='gesperrt', value='1'))
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/aufstellung/generieren', methods=['POST'])
@admin_required
def aufstellung_generieren():
    # Only (re)generate upcoming dates; past lineups are history and stay as they are.
    heute = date.today().isoformat()
    alle_termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    vergangene_ids = {t.id for t in alle_termine if t.datum < heute}
    termine = [t for t in alle_termine if t.datum >= heute]

    zukuenftige_ids = [t.id for t in termine]
    if zukuenftige_ids:
        Aufstellung.query.filter(Aufstellung.termin_id.in_(zukuenftige_ids)).delete(synchronize_session=False)

    # Start the fairness count from games already played.
    kinder = Kind.query.all()
    play_counts = {k.id: 0 for k in kinder}
    for a in Aufstellung.query.filter_by(rolle='spieler').all():
        if a.termin_id in vergangene_ids:
            play_counts[a.kind_id] = play_counts.get(a.kind_id, 0) + 1

    for termin in termine:
        verfuegbare_ids = [
            v.kind_id for v in
            Verfuegbarkeit.query.filter_by(termin_id=termin.id, verfuegbar=True).all()
        ]
        if not verfuegbare_ids:
            continue

        verbleibend = set(verfuegbare_ids)
        spieler_per_mannschaft = {}

        # Pass 1: assign 4 spieler per team in order of play count
        for spiel in sorted(termin.spiele, key=lambda s: s.mannschaft):
            m = spiel.mannschaft
            pool = Kind.query.filter(Kind.id.in_(verbleibend)).all()
            pool.sort(key=lambda k: (play_counts.get(k.id, 0), k.name))
            spieler = pool[:4]
            spieler_per_mannschaft[m] = spieler
            for kind in spieler:
                play_counts[kind.id] += 1
                verbleibend.discard(kind.id)

        # Pass 2: remaining kids are ersatz — available for all teams on this date
        ersatz_pool = Kind.query.filter(Kind.id.in_(verbleibend)).all()
        ersatz_pool.sort(key=lambda k: k.name)

        for spiel in termin.spiele:
            m = spiel.mannschaft
            for i, kind in enumerate(spieler_per_mannschaft.get(m, []), start=1):
                db.session.add(Aufstellung(termin_id=termin.id, kind_id=kind.id,
                                           mannschaft=m, rolle='spieler', position=i))
            for i, kind in enumerate(ersatz_pool, start=1):
                db.session.add(Aufstellung(termin_id=termin.id, kind_id=kind.id,
                                           mannschaft=m, rolle='ersatz', position=i))

    db.session.commit()
    return redirect(url_for('aufstellung'))


@app.route('/admin/aufstellung/reorder', methods=['POST'])
@admin_required
def aufstellung_reorder():
    lists = request.get_json().get('lists', [])
    for lst in lists:
        Aufstellung.query.filter_by(
            termin_id=lst['termin_id'],
            mannschaft=lst['mannschaft'],
            rolle=lst['rolle']
        ).delete()
        for i, kind_id in enumerate(lst['kind_ids'], start=1):
            db.session.add(Aufstellung(
                termin_id=lst['termin_id'],
                kind_id=int(kind_id),
                mannschaft=lst['mannschaft'],
                rolle=lst['rolle'],
                position=i
            ))
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/aufstellung/<int:termin_id>/<mannschaft>/spieler/<int:kind_id>/abwesend', methods=['POST'])
@admin_required
def spieler_abwesend(termin_id, mannschaft, kind_id):
    eintrag = Aufstellung.query.filter_by(termin_id=termin_id, mannschaft=mannschaft,
                                          kind_id=kind_id, rolle='spieler').first()
    if not eintrag:
        return redirect(url_for('aufstellung'))

    freie_position = eintrag.position
    db.session.delete(eintrag)

    erster_ersatz = (Aufstellung.query
                     .filter_by(termin_id=termin_id, mannschaft=mannschaft, rolle='ersatz')
                     .order_by(Aufstellung.position).first())
    if erster_ersatz:
        erster_ersatz.rolle = 'spieler'
        erster_ersatz.position = freie_position
        restliche = (Aufstellung.query
                     .filter_by(termin_id=termin_id, mannschaft=mannschaft, rolle='ersatz')
                     .order_by(Aufstellung.position).all())
        for i, e in enumerate(restliche, start=1):
            e.position = i

    db.session.commit()
    return redirect(url_for('aufstellung'))


@app.route('/aufstellung/<int:termin_id>/<mannschaft>/ersatz/<int:kind_id>/hoch', methods=['POST'])
@admin_required
def ersatz_hoch(termin_id, mannschaft, kind_id):
    eintrag = Aufstellung.query.filter_by(termin_id=termin_id, mannschaft=mannschaft,
                                          kind_id=kind_id, rolle='ersatz').first()
    if not eintrag or eintrag.position <= 1:
        return redirect(url_for('aufstellung'))
    nachbar = Aufstellung.query.filter_by(termin_id=termin_id, mannschaft=mannschaft,
                                          rolle='ersatz', position=eintrag.position - 1).first()
    if nachbar:
        nachbar.position, eintrag.position = eintrag.position, eintrag.position - 1
    db.session.commit()
    return redirect(url_for('aufstellung'))


@app.route('/aufstellung/<int:termin_id>/<mannschaft>/ersatz/<int:kind_id>/runter', methods=['POST'])
@admin_required
def ersatz_runter(termin_id, mannschaft, kind_id):
    eintrag = Aufstellung.query.filter_by(termin_id=termin_id, mannschaft=mannschaft,
                                          kind_id=kind_id, rolle='ersatz').first()
    if not eintrag:
        return redirect(url_for('aufstellung'))
    nachbar = Aufstellung.query.filter_by(termin_id=termin_id, mannschaft=mannschaft,
                                          rolle='ersatz', position=eintrag.position + 1).first()
    if nachbar:
        nachbar.position, eintrag.position = eintrag.position, eintrag.position + 1
    db.session.commit()
    return redirect(url_for('aufstellung'))


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

with app.app_context():
    db.create_all()
    from sqlalchemy import inspect, text
    with db.engine.connect() as conn:
        auf_cols = [c['name'] for c in inspect(db.engine).get_columns('aufstellung')]
        if 'mannschaft' not in auf_cols:
            conn.execute(text('ALTER TABLE aufstellung ADD COLUMN mannschaft VARCHAR(10)'))
            conn.commit()
        spiel_cols = [c['name'] for c in inspect(db.engine).get_columns('spiel')]
        if 'maps_link' not in spiel_cols:
            conn.execute(text('ALTER TABLE spiel ADD COLUMN maps_link VARCHAR(500)'))
            conn.commit()

if __name__ == '__main__':
    app.run(debug=True)
