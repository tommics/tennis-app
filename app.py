import os
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bitte-in-produktion-aendern')
_db_path = os.environ.get('DATABASE_PATH', '')
if _db_path:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_db_path}'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///tennis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'tennis123')


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


def get_kind_name():
    return request.cookies.get('kind_name', '').strip()


# ---------------------------------------------------------------------------
# Parent routes
# ---------------------------------------------------------------------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
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
        kind = Kind.query.filter_by(name=kind_name).first()
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

    kind = Kind.query.filter_by(name=kind_name).first()
    verfuegbarkeiten = {}
    if kind:
        for v in Verfuegbarkeit.query.filter_by(kind_id=kind.id).all():
            verfuegbarkeiten[v.termin_id] = v.verfuegbar

    return render_template('verfuegbarkeit.html',
                           termine=termine,
                           verfuegbarkeiten=verfuegbarkeiten,
                           gesperrt=gesperrt,
                           kind_name=kind_name)


@app.route('/uebersicht')
def uebersicht():
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    kinder = Kind.query.order_by(Kind.name).all()

    matrix = {}
    for kind in kinder:
        matrix[kind.id] = {}
        for termin in termine:
            v = Verfuegbarkeit.query.filter_by(kind_id=kind.id, termin_id=termin.id).first()
            matrix[kind.id][termin.id] = v.verfuegbar if v else False

    return render_template('uebersicht.html',
                           termine=termine,
                           kinder=kinder,
                           matrix=matrix,
                           gesperrt=ist_gesperrt())


@app.route('/aufstellung')
def aufstellung():
    gesperrt = ist_gesperrt()
    termine = Spieltermin.query.order_by(Spieltermin.datum).all()

    lineup = {}
    for termin in termine:
        lineup[termin.id] = {}
        for spiel in termin.spiele:
            m = spiel.mannschaft
            spieler_eintraege = (Aufstellung.query
                                 .filter_by(termin_id=termin.id, mannschaft=m, rolle='spieler')
                                 .order_by(Aufstellung.position).all())
            ersatz_eintraege = (Aufstellung.query
                                .filter_by(termin_id=termin.id, mannschaft=m, rolle='ersatz')
                                .order_by(Aufstellung.position).all())
            lineup[termin.id][m] = {
                'spieler': [(e, db.session.get(Kind, e.kind_id)) for e in spieler_eintraege],
                'ersatz':  [(e, db.session.get(Kind, e.kind_id)) for e in ersatz_eintraege],
            }

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
        if request.form.get('passwort', '') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin'))
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
    Aufstellung.query.delete()

    termine = Spieltermin.query.order_by(Spieltermin.datum).all()
    kinder = Kind.query.all()
    play_counts = {k.id: 0 for k in kinder}

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
