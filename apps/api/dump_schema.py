from infrastructure.db.models import Base

for t in Base.metadata.sorted_tables:
    print(f"--- {t.name} ---")
    for c in t.columns:
        fks = [str(f.target_fullname) for f in c.foreign_keys]
        fk_note = f" -> FK: {fks}" if fks else ""
        print(f"  {c.name} ({c.type}){fk_note}")