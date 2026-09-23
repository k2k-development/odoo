from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.tests import tagged

from odoo.addons.account_edi_ubl_cii.tests.common import TestUblCiiBECommon


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

    @freeze_time('2020-01-01')
    def test_import_tax_total_several_subtotals_same_rate(self):
        tax_21 = self.percent_tax(21.0)

        invoice = self._import_invoice_as_attachment_on(
            test_name='test_import_tax_total_several_subtotals_same_rate',
            journal=self.company_data['default_journal_sale'],
        )

        self.assertRecordValues(
            invoice.invoice_line_ids,
            [
                {'price_unit': 100.0, 'tax_ids': tax_21.ids},
                {'price_unit': 200.0, 'tax_ids': tax_21.ids},
            ],
        )
        # The document declares 21.01 + 42.00 in two subtotals of the same rate while Odoo computes 63.00:
        # the tax line must be aligned with the sum of the subtotals, not with the last one.
        self.assertRecordValues(
            invoice,
            [
                {
                    'amount_untaxed': 300.0,
                    'amount_tax': 63.01,
                    'amount_total': 363.01,
                },
            ],
        )

    @freeze_time('2020-01-01')
    def test_import_tax_total_correction_follows_repartition_factors(self):
        # A tax booked half on the tax account and half on another account (e.g. partially deductible VAT).
        tax_account = self.company_data['default_account_tax_sale']
        other_account = self.copy_account(tax_account)
        repartition_lines = [
            Command.create({'repartition_type': 'base'}),
            Command.create({'repartition_type': 'tax', 'factor_percent': 50, 'account_id': tax_account.id}),
            Command.create({'repartition_type': 'tax', 'factor_percent': 50, 'account_id': other_account.id}),
        ]
        tax_21 = self.percent_tax(
            21.0,
            invoice_repartition_line_ids=repartition_lines,
            refund_repartition_line_ids=repartition_lines,
        )

        invoice = self._import_invoice_as_attachment_on(
            test_name='test_import_tax_total_correction_repartition_lines',
            journal=self.company_data['default_journal_sale'],
        )

        self.assertRecordValues(invoice.invoice_line_ids, [{'price_unit': 100.0, 'tax_ids': tax_21.ids}])
        # The document declares 21.02 while Odoo computes 21.00: the difference follows the repartition factors
        # instead of landing entirely on the first tax line.
        self.assertRecordValues(
            invoice.line_ids.filtered(lambda line: line.display_type == 'tax').sorted(lambda line: line.tax_repartition_line_id.id),
            [
                {'account_id': tax_account.id, 'amount_currency': -10.51},
                {'account_id': other_account.id, 'amount_currency': -10.51},
            ],
        )
        self.assertRecordValues(
            invoice,
            [
                {
                    'amount_untaxed': 100.0,
                    'amount_tax': 21.02,
                    'amount_total': 121.02,
                },
            ],
        )

    @freeze_time('2020-01-01')
    def test_import_tax_total_correction_skips_taxes_without_net_amount(self):
        # A tax whose repartition lines cancel each other (+100% / -100%, used to book a memo entry next to the
        # real tax) cannot absorb a difference: correcting one of its lines unbalances the pair while the real
        # tax amount stays wrong.
        tax_account = self.company_data['default_account_tax_sale']
        memo_debit_account = self.copy_account(tax_account)
        memo_credit_account = self.copy_account(tax_account)
        repartition_lines = [
            Command.create({'repartition_type': 'base'}),
            Command.create({'repartition_type': 'tax', 'account_id': tax_account.id}),
        ]
        tax_21 = self.percent_tax(
            21.0,
            invoice_repartition_line_ids=repartition_lines,
            refund_repartition_line_ids=repartition_lines,
        )
        memo_repartition_lines = [
            Command.create({'repartition_type': 'base'}),
            Command.create({'repartition_type': 'tax', 'factor_percent': 100, 'account_id': memo_debit_account.id}),
            Command.create({'repartition_type': 'tax', 'factor_percent': -100, 'account_id': memo_credit_account.id}),
        ]
        memo_tax_21 = self.percent_tax(
            21.0,
            invoice_repartition_line_ids=memo_repartition_lines,
            refund_repartition_line_ids=memo_repartition_lines,
        )

        invoice = self._import_invoice_as_attachment_on(
            test_name='test_import_tax_total_correction_repartition_lines',
            journal=self.company_data['default_journal_sale'],
        )
        self.assertRecordValues(invoice.invoice_line_ids, [{'price_unit': 100.0, 'tax_ids': tax_21.ids}])

        # Add the memo tax to the line in such a way that its tax lines come before the ones of the real tax (as
        # when the memo tax has a lower sequence), then run the correction again.
        with invoice._get_edi_creation() as invoice:
            invoice.invoice_line_ids.tax_ids = memo_tax_21
        with invoice._get_edi_creation() as invoice:
            invoice.invoice_line_ids.tax_ids = memo_tax_21 + tax_21
        tax_lines = invoice.line_ids.filtered(lambda line: line.display_type == 'tax')
        self.assertRecordValues(
            tax_lines,
            [
                {'account_id': memo_debit_account.id, 'amount_currency': -21.0},
                {'account_id': memo_credit_account.id, 'amount_currency': 21.0},
                {'account_id': tax_account.id, 'amount_currency': -21.0},
            ],
        )

        _filename, content = self._import_file_content('test_import_tax_total_correction_repartition_lines', 'xml')
        with invoice._get_edi_creation() as invoice:
            self.env['account.edi.xml.ubl_21']._correct_invoice_tax_amount(etree.fromstring(content), invoice)

        self.assertRecordValues(
            tax_lines,
            [
                {'account_id': memo_debit_account.id, 'amount_currency': -21.0},
                {'account_id': memo_credit_account.id, 'amount_currency': 21.0},
                {'account_id': tax_account.id, 'amount_currency': -21.02},
            ],
        )
        self.assertRecordValues(
            invoice,
            [
                {
                    'amount_untaxed': 100.0,
                    'amount_tax': 21.02,
                    'amount_total': 121.02,
                },
            ],
        )
