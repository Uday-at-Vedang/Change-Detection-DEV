import logging

from sqlalchemy.orm import Session

from .models import DdaVillage, DdaZone
from .path_slugs import unique_slug
from .seed_data import DELHI_ZONES

logger = logging.getLogger(__name__)


def seed_delhi_hierarchy(db: Session) -> dict:
    """Insert Zone → Village hierarchy if empty. Idempotent."""
    existing = db.query(DdaZone).count()
    if existing > 0:
        from .library_migration import backfill_slugs
        backfill_slugs(db)
        return {"seeded": False, "zones": existing}

    zones_created = 0
    villages_created = 0
    zone_slugs: set = set()
    for zone_name, villages in DELHI_ZONES.items():
        slug = unique_slug(zone_name, zone_slugs)
        zone_slugs.add(slug)
        zone = DdaZone(name=zone_name, slug=slug, mode="admin")
        db.add(zone)
        db.flush()
        zones_created += 1
        folder_slugs: set = set()
        for village_name in villages:
            fslug = unique_slug(village_name, folder_slugs)
            folder_slugs.add(fslug)
            db.add(DdaVillage(zone_id=zone.id, name=village_name, slug=fslug))
            villages_created += 1

    db.commit()
    logger.info("DDA seed: %d zones, %d villages", zones_created, villages_created)
    return {"seeded": True, "zones": zones_created, "villages": villages_created}
