import logging
import requests
from datetime import datetime
from json import JSONDecodeError

from odoo import _
from odoo.exceptions import UserError
from odoo.addons.l10n_tr_nilvera.const import NILVERA_ERROR_CODE_MESSAGES

_logger = logging.getLogger(__name__)

def _get_nilvera_client(company, timeout_limit=None):
    return NilveraClient(
        test_environment=company.l10n_tr_nilvera_use_test_env,
        api_key=company.sudo().l10n_tr_nilvera_api_key,
        timeout_limit=timeout_limit,
    )


class NilveraClient:
    def __init__(self, test_environment=False, api_key=None, timeout_limit=None):
        self.is_production = not test_environment
        self.base_url = 'https://api.nilvera.com' if self.is_production else 'https://apitest.nilvera.com'
        self.timeout_limit = min(timeout_limit or 10, 30)

        self.__session = requests.Session()
        self.__session.headers.update({'Accept': 'application/json'})
        if api_key:
            self.__session.headers['Authorization'] = 'Bearer ' + api_key

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if hasattr(self, '_NilveraClient__session'):
            self.__session.close()

    def request(self, method, endpoint, params=None, json=None, files=None, handle_response=True):
        start = datetime.utcnow()
        url = self.base_url + endpoint

        try:
            response = self.__session.request(
                method, url,
                timeout=self.timeout_limit,
                params=params,
                json=json,
                files=files,
            )
        except requests.exceptions.RequestException as e:
            _logger.info(_("Network error during request: %s"), e)
            raise UserError(_("Network connectivity issue. Please check your internet connection and try again."))

        end = datetime.utcnow()
        duration = (end - start).total_seconds()
        self._log_request(method, duration, url, response.status_code)

        if handle_response:
            return self.handle_response(response)
        return response

    def _log_request(self, method, duration, url, status_code):
        _logger.info(
            '"%(method)s %(url)s" %(status)s %(duration).3f',
            {
                'method': method,
                'url': url,
                'status': status_code,
                'duration': duration,
            },
        )

    def handle_response(self, response):
        if response.status_code in {401, 403}:
            raise UserError(_("Oops, seems like you're unauthorised to do this. Try another API key with more rights or contact Nilvera."))
        elif response.status_code >= 400:
            raise UserError(self._format_error_message(response))

        try:
            return response.json()
        except JSONDecodeError:
            _logger.exception(_("Invalid JSON response: %s", response.text))
            raise UserError(_("An error occurred. Try again later."))

    def _format_error_message(self, response):
        """ Build a readable message out of an error response.

        Nilvera describes errors as ``{"Message": ..., "Errors": [{"Code": ..., "Description": ..., "Detail": ...}]}``.
        When the body is not such a payload (HTML error page, empty body), only the status code and its reason
        can be reported.
        """
        try:
            payload = response.json()
        except ValueError:  # JSONDecodeError
            payload = None

        if not isinstance(payload, dict):
            return _("Odoo could not perform this action at the moment, try again later.\n%s - %s", response.reason, response.status_code)

        lines = [_(
            "Nilvera rejected the request (HTTP %(status)s): %(message)s",
            status=response.status_code,
            message=payload.get('Message') or response.reason,
        )]
        for error in payload.get('Errors') or []:
            if not isinstance(error, dict):
                lines.append(f" - {error}")
                continue
            code = error.get('Code')
            known_description = NILVERA_ERROR_CODE_MESSAGES.get(code)
            description = str(known_description) if known_description is not None else error.get('Description')
            parts = [str(part) for part in (code, description, error.get('Detail')) if part not in (None, '')]
            if parts:
                lines.append(" - " + ": ".join(parts))
        return "\n".join(lines)
