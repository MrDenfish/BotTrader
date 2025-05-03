
from sqlalchemy import (MetaData)
from sqlalchemy.ext.declarative import declarative_base

# Metadata object for async database usage
metadata = MetaData()

Base = declarative_base(metadata=metadata)


