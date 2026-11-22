from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.config import Options
from app.pipeline import Audit

router = APIRouter(prefix='/api/substitutes')


@router.get('')
def inspect(request: Request, options: Annotated[Options, Query()]):
    audit = Audit(request.app.state.settings, options)
    return {'rows': audit.clearances['rows'], 'filing_rows': audit.clearances['filing_rows'],
            'quarantine': audit.clearances['quarantine']}
