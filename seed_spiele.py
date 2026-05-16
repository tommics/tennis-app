"""One-time script to seed all Bayer Dormagen match dates from Gruppeneinteilung.pdf."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db, Spieltermin, Spiel

MATCHES = [
    # (datum YYYY-MM-DD, mannschaft, uhrzeit, gegner, heimspiel)
    # BD 1 - Gruppe M4
    ("2026-05-30", "BD 1", "11:00", "bei TuS rrh Köln",             False),
    ("2026-06-13", "BD 1", "11:00", "bei KTG BG Köln",              False),
    ("2026-06-27", "BD 1", "11:00", "TuS rrh / RW Leverkusen / TC Weiden", True),
    ("2026-09-12", "BD 1", "10:00", "bei RW Leverkusen",            False),
    # BD 2 - Gruppe M3
    ("2026-06-13", "BD 2", "11:00", "Rondorf / KTG BG / TGL",       True),
    ("2026-06-27", "BD 2", "11:00", "bei TG Leverkusen",            False),
    ("2026-07-11", "BD 2", "11:00", "bei TC Rondorf",               False),
    ("2026-09-12", "BD 2", "10:00", "bei TC RS Neubrück",           False),
]

with app.app_context():
    added_termine = 0
    added_spiele = 0
    skipped = 0

    for datum, mannschaft, uhrzeit, gegner, heimspiel in MATCHES:
        # Get or create Spieltermin
        termin = Spieltermin.query.filter_by(datum=datum).first()
        if not termin:
            termin = Spieltermin(datum=datum)
            db.session.add(termin)
            db.session.flush()
            added_termine += 1

        # Check if Spiel already exists for this termin + mannschaft
        existing = Spiel.query.filter_by(termin_id=termin.id, mannschaft=mannschaft).first()
        if existing:
            print(f"  SKIP  {datum} {mannschaft} (already exists)")
            skipped += 1
            continue

        spiel = Spiel(
            termin_id=termin.id,
            mannschaft=mannschaft,
            uhrzeit=uhrzeit,
            gegner=gegner,
            heimspiel=heimspiel,
        )
        db.session.add(spiel)
        added_spiele += 1
        heim_str = "Heim" if heimspiel else "Auswärts"
        print(f"  ADD   {datum} {mannschaft} {uhrzeit} {heim_str:8s} vs {gegner}")

    db.session.commit()
    print(f"\nDone. Termine added: {added_termine}, Spiele added: {added_spiele}, Skipped: {skipped}")
