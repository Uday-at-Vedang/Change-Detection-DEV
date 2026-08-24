import logging

from sqlalchemy.orm import Session

from .models import DdaVillage, DdaZone
from .seed_data import DELHI_ZONES

logger = logging.getLogger(__name__)


def seed_delhi_hierarchy(db: Session) -> dict:
    """Insert Zone → Village hierarchy if empty. Idempotent."""
    existing = db.query(DdaZone).count()
    if existing > 0:
        return {"seeded": False, "zones": existing}

    zones_created = 0
    villages_created = 0
    for zone_name, villages in DELHI_ZONES.items():
        zone = DdaZone(name=zone_name, mode="admin")
        db.add(zone)
        db.flush()
        zones_created += 1
        for village_name in villages:
            db.add(DdaVillage(zone_id=zone.id, name=village_name))
            villages_created += 1

    db.commit()
    logger.info("Vedangsoft seed: %d zones, %d villages", zones_created, villages_created)
    return {"seeded": True, "zones": zones_created, "villages": villages_created}
