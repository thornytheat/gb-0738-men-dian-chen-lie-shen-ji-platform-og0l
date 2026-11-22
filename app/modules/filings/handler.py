from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.config import Options
from app.pipeline import Audit

router = APIRouter(prefix='/api/filings')


@router.get('')
def inspect(request: Request, options: Annotated[Options, Query()]):
    audit = Audit(request.app.state.settings, options)
    shelves = [{**row['filing'], 'status': row['status'], 'assessed_cents': row['assessed_cents'],
                'offset_cents': row['offset_cents'], 'original_cents': row['original_cents']}
               for row in audit.penalties['rows']]
    return {'present': audit.filings['present'], 'rows': audit.filings['rows'],
            'quarantine': audit.filings['quarantine'], 'scopes': audit.filings['scopes'],
            'shelves': shelves, 'counts': audit.settlement['counts'],
            'assessed_cents': audit.settlement['assessed_cents'],
            'offset_cents': audit.settlement['offset_cents'],
            'original_cents': audit.settlement['original_cents']}
