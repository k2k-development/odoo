from odoo.addons.account_edi_ubl_cii.tests.common import TestUblCiiBECommon
from odoo.tests import tagged

from freezegun import freeze_time


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestUblImportUbl21InvoiceBE(TestUblCiiBECommon):
    """ Plain UBL 2.1 documents (no Peppol customization) are decoded by 'account.edi.xml.ubl_21'. """

    @classmethod
    def subfolders(cls):
        return 'ubl_21', 'invoice', 'be'

    @freeze_time('2020-01-01')
    def test_import_cash_rounding_taxed_lines(self):
        tax_21 = self.percent_tax(21.0)

        invoice = self._import_invoice_as_attachment_on(
            test_name='test_import_cash_rounding_two_lines',
            journal=self.company_data['default_journal_sale'],
        )

        self.assertRecordValues(
            invoice.invoice_line_ids,
            [
                {'price_unit': 292.82, 'tax_ids': tax_21.ids, 'amount_currency': -292.82, 'balance': -292.82},
                {'price_unit': 101.10, 'tax_ids': tax_21.ids, 'amount_currency': -101.10, 'balance': -101.10},
                {'price_unit': 0.01, 'tax_ids': [], 'amount_currency': -0.01, 'balance': -0.01},
            ],
        )
        self.assertRecordValues(
            invoice,
            [
                {
                    'amount_untaxed': 393.93,
                    'amount_tax': 82.72,
                    'amount_total': 476.65,
                    'amount_total_signed': 476.65,
                },
            ],
        )

    @freeze_time('2020-01-01')
    def test_import_cash_rounding_untaxed_lines(self):
        # No 21% tax exists: the lines are imported without taxes. The rounding line must not
        # prevent the ORM from computing the amounts of the other lines.
        invoice = self._import_invoice_as_attachment_on(
            test_name='test_import_cash_rounding_two_lines',
            journal=self.company_data['default_journal_sale'],
        )

        self.assertRecordValues(
            invoice.invoice_line_ids,
            [
                {'price_unit': 292.82, 'tax_ids': [], 'amount_currency': -292.82, 'balance': -292.82},
                {'price_unit': 101.10, 'tax_ids': [], 'amount_currency': -101.10, 'balance': -101.10},
                {'price_unit': 0.01, 'tax_ids': [], 'amount_currency': -0.01, 'balance': -0.01},
            ],
        )
        self.assertRecordValues(
            invoice,
            [
                {
                    'amount_untaxed': 393.93,
                    'amount_tax': 0.0,
                    'amount_total': 393.93,
                    'amount_total_signed': 393.93,
                },
            ],
        )
