"""Measure Release routing/validation/encoding, without logging request contents."""
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException, Response
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from deepyield.i18n import language, translate, localize_report

from deepyield.compute.release_control import request_trace
from deepyield.compute.release_performance_log import emit, resources, enabled


class ReleaseTimingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def measured(request):
            if '/jobs/' in self.path or not enabled():
                return await handler(request)
            trace = {'id': uuid4().hex, 'started': perf_counter()}
            token = request_trace.set(trace)
            status = 500
            emit('http_start', request_id=trace['id'], method=request.method,
                 route=self.path, **resources())
            try:
                response = await handler(request)
                status = response.status_code
                response.headers['X-Release-Request-ID'] = trace['id']
                trace['response_bytes'] = len(getattr(response, 'body', b''))
                return response
            except RequestValidationError:
                status = 422
                raise
            except Exception as exc:
                status = getattr(exc, 'status_code', 500)
                raise
            finally:
                end = perf_counter()
                fields = {name: trace[name] for name in ('response_bytes',) if name in trace}
                if 'service_started' in trace:
                    fields['before_service_ms'] = round((trace['service_started'] - trace['started']) * 1000, 3)
                if 'service_finished' in trace:
                    fields['post_service_ms'] = round((end - trace['service_finished']) * 1000, 3)
                emit('http_end', request_id=trace['id'], method=request.method,
                     route=self.path, status=status,
                     handler_ms=round((end - trace['started']) * 1000, 3), **fields, **resources())
                request_trace.reset(token)

        async def localized(request):
            # The browser keeps canonical Chinese responses in memory and translates at
            # render time. Explicit report requests select their output language here.
            locale = request.query_params.get('lang', 'zh')
            token = language.set('en' if locale == 'en' else 'zh')
            try:
                try:
                    response = await measured(request)
                except HTTPException as exc:
                    if isinstance(exc.detail, str):
                        exc.detail = translate(exc.detail)
                    raise
                if language.get() == 'en' and response.headers.get('content-type', '').startswith('application/json'):
                    body = getattr(response, 'body', None)
                    if body is None and getattr(response, 'path', None):
                        # Spooled exports stream from disk; translate by replacing
                        # the response instead of buffering inside the route.
                        body = Path(response.path).read_bytes()
                    if body is not None:
                        payload = json.loads(body)
                        headers = {name: value for name, value in response.headers.items()
                                   if name.lower() != 'content-length'}
                        translated = Response(
                            json.dumps(localize_report(payload), ensure_ascii=False,
                                       separators=(',', ':')).encode('utf-8'),
                            media_type='application/json', headers=headers)
                        translated.background = getattr(response, 'background', None)
                        response = translated
                response.headers.setdefault('Content-Language', language.get())
                return response
            finally:
                language.reset(token)

        return localized
