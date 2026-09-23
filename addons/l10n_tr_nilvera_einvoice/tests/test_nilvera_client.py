import json
from datetime import datetime, timedelta
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged

from odoo.addons.l10n_tr_nilvera.lib.nilvera_client import NilveraClient
from odoo.addons.l10n_tr_nilvera_einvoice.tests.test_xml_ubl_tr_common import (
    TestUBLTRCommon,
)

UNAUTHORISED_MESSAGE = "Oops, seems like you're unauthorised to do this. Try another API key with more rights or contact Nilvera."
NILVERA_400_BODY = {
    "Message": "GECERSIZ ISTEK",
    "Errors": [{
        "Code": 400,
        "Detail": "StartDate ile EndDate arasında en fazla 6 ay olabilir.",
    }],
}


def _http_response(status_code, body=None, text=None, reason='Bad Request'):
    """ Build a real requests.Response so that .json() and .text behave like in production. """
    response = requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.url = 'https://apitest.nilvera.com/einvoice/Purchase'
    if body is not None:
        response._content = json.dumps(body).encode()
        response.headers['Content-Type'] = 'application/json'
    else:
        response._content = (text or '').encode()
        response.headers['Content-Type'] = 'text/html'
    return response


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestNilveraClient(TestUBLTRCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['account.journal'].create({
            'name': 'TR Journal',
            'code': 'TRJ',
            'type': 'purchase',
            'company_id': cls.company_data['company'].id,
        })

    def test_handle_response(self):
        client = NilveraClient(test_environment=True)

        # A successful response is returned parsed.
        self.assertEqual(client.handle_response(_http_response(200, {'TotalPages': 1})), {'TotalPages': 1})

        # Authentication errors keep their dedicated message.
        with self.assertRaises(UserError) as error:
            client.handle_response(_http_response(401, text='Unauthorized', reason='Unauthorized'))
        self.assertEqual(str(error.exception), UNAUTHORISED_MESSAGE)

        # A rejected request (400) reports Nilvera's message and every error detail.
        with self.assertRaises(UserError) as error:
            client.handle_response(_http_response(400, NILVERA_400_BODY))
        for expected in ('400', 'GECERSIZ ISTEK', 'StartDate ile EndDate arasında en fazla 6 ay olabilir.'):
            self.assertIn(expected, str(error.exception))

        # Known error codes are described.
        with self.assertRaises(UserError) as error:
            client.handle_response(_http_response(422, {
                "Message": "HATALI ISTEK",
                "Errors": [{"Code": 2000, "Description": "Yeterli Kontörünüz Bulunmamaktadır."}],
            }, reason='Unprocessable Entity'))
        self.assertIn('You do not have sufficient credits', str(error.exception))

        # Errors without a JSON body report the status code and its reason.
        with self.assertRaises(UserError) as error:
            client.handle_response(_http_response(502, text='<html>Bad Gateway</html>', reason='Bad Gateway'))
        self.assertIn('502', str(error.exception))
        self.assertIn('Bad Gateway', str(error.exception))

    def test_fetch_documents_reports_rejected_listing(self):
        # The listing used to be returned as if it were a page without documents: nothing was fetched, nothing was said.
        with patch.object(requests.Session, 'request', return_value=_http_response(400, NILVERA_400_BODY)), \
             patch.object(self.env.cr, 'commit', autospec=True), \
             self.assertRaises(UserError) as error:
            self.env['account.move']._l10n_tr_nilvera_get_documents()
        self.assertIn('StartDate ile EndDate arasında en fazla 6 ay olabilir.', str(error.exception))

    def test_get_status_continues_after_a_rejected_document(self):
        _, rejected = self._generate_invoice_xml(self.einvoice_partner, include_invoice=True)
        _, accepted = self._generate_invoice_xml(self.einvoice_partner, include_invoice=True, name='EIN/2025/2')
        rejected.l10n_tr_nilvera_uuid = 'rejected_uuid'
        accepted.l10n_tr_nilvera_uuid = 'accepted_uuid'

        def mock_request(method, url, *args, **kwargs):
            if 'rejected_uuid' in url:
                message = "Nilvera rejected the request (HTTP 400): GECERSIZ ISTEK"
                raise UserError(message)
            return {'InvoiceStatus': {'Code': 'succeed', 'Description': 'text', 'DetailDescription': 'text'}}

        with patch.object(NilveraClient, 'request', side_effect=mock_request):
            (rejected + accepted)._l10n_tr_nilvera_get_submitted_document_status()

        self.assertEqual(accepted.l10n_tr_nilvera_send_status, 'succeed')
        self.assertNotEqual(rejected.l10n_tr_nilvera_send_status, 'succeed')
        self.assertIn('GECERSIZ ISTEK', str(rejected.message_ids[0].body))

    @freeze_time('2026-09-21 09:00:00')
    def test_fetch_documents_splits_long_ranges(self):
        # Nilvera rejects a range longer than 6 months: 400 days are fetched in windows of at most 180 days.
        param_key = f"l10n_tr_nilvera_einvoice_purchase.last_fetched_date.{self.company_data['company'].id}"
        self.env['ir.config_parameter'].sudo().set_param(param_key, '2025-08-17')

        with patch.object(NilveraClient, 'request', return_value={'TotalPages': 0, 'Content': []}) as mocked_request, \
             patch.object(self.env.cr, 'commit', autospec=True):
            self.env['account.move']._l10n_tr_nilvera_get_documents()

        listings = [call.kwargs['params'] for call in mocked_request.call_args_list]
        self.assertEqual(len(listings), 3)
        previous_end = None
        for params in listings:
            start = datetime.fromisoformat(params['StartDate'])
            end = datetime.fromisoformat(params['EndDate'])
            self.assertLessEqual(end - start, timedelta(days=180), params)
            if previous_end:
                self.assertEqual(start, previous_end, "the windows must be contiguous")
            previous_end = end
            self.assertEqual(
                {key: value for key, value in params.items() if key not in ('StartDate', 'EndDate')},
                {'StatusCode': ['succeed'], 'DateFilterType': 'CreatedDate', 'SortColumn': 'CreationDateTime', 'SortType': 'ASC', 'Page': 1},
            )
        self.assertEqual(listings[0]['StartDate'], '2025-08-17')
        self.assertEqual(listings[-1]['EndDate'], '2026-09-21T12:00:00')  # now, in Europe/Istanbul
        # The completed windows are saved so that an interrupted run resumes from them; the last window is only
        # advanced by the fetched documents.
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_param(param_key), listings[-1]['StartDate'])
