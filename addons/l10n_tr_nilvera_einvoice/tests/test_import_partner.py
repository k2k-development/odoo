from lxml import etree

from odoo.tests import tagged
from odoo.tools import file_open

from odoo.addons.l10n_tr_nilvera_einvoice.tests.test_xml_ubl_tr_common import (
    TestUBLTRCommon,
)

CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS = {'cbc': CBC, 'cac': CAC}
SUPPLIER_NAME = 'Anadolu Elektrik Dağıtım A.Ş.'
SUPPLIER_VKN = '1234567018'  # passes the VKN checksum
OTHER_VKN = '1234567026'  # passes the VKN checksum
INVALID_VKN = '1234567891'  # fails the VKN checksum
PERSON_TCKN = '17291716060'
MERSIS_NUMBER = '0454001395800010'
TRADE_REGISTRY_NUMBER = '313312-0'


@tagged('post_install_l10n', 'post_install', '-at_install')
class TestImportPartner(TestUBLTRCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with file_open('l10n_tr_nilvera_einvoice/tests/test_files/partner/supplier_vkn.xml', 'rb') as xml_file:
            cls.supplier_xml = xml_file.read()

    def _supplier_tree(self, identifications=None, company_id=None, country_code=None, country_name='TÜRKİYE'):
        """ Variants of the fixture whose supplier carries the trade registry number, the MERSIS number and the
        VKN as cac:PartyIdentification, no cbc:CompanyID, and a country given by its name only.

        :param identifications: [(schemeID, value), ...] replacing the cac:PartyIdentification nodes.
        :param company_id: value of a cbc:CompanyID added to the cac:PartyTaxScheme and the cac:PartyLegalEntity.
        :param country_code: cbc:IdentificationCode of the country (None for no code).
        :param country_name: cbc:Name of the country (None for no name).
        """
        tree = etree.fromstring(self.supplier_xml)
        party = tree.find('./cac:AccountingSupplierParty/cac:Party', NS)
        if identifications is not None:
            for node in party.findall('./cac:PartyIdentification', NS):
                party.remove(node)
            for position, (scheme, value) in enumerate(identifications):
                identification = etree.Element(f'{{{CAC}}}PartyIdentification')
                etree.SubElement(identification, f'{{{CBC}}}ID', schemeID=scheme).text = value
                party.insert(position, identification)
        if company_id:
            tax_scheme = party.find('./cac:PartyTaxScheme', NS)
            tax_scheme.insert(0, etree.Element(f'{{{CBC}}}CompanyID'))
            tax_scheme[0].text = company_id
            etree.SubElement(party.find('./cac:PartyLegalEntity', NS), f'{{{CBC}}}CompanyID').text = company_id
        country = party.find('./cac:PostalAddress/cac:Country', NS)
        for node in list(country):
            country.remove(node)
        if country_code:
            etree.SubElement(country, f'{{{CBC}}}IdentificationCode').text = country_code
        if country_name:
            etree.SubElement(country, f'{{{CBC}}}Name').text = country_name
        return tree

    def _supplier_vals(self, **kwargs):
        return self.env['account.edi.xml.ubl.tr']._import_retrieve_partner_vals(self._supplier_tree(**kwargs), 'AccountingSupplier')

    def _import(self, tree=None):
        xml = etree.tostring(tree, xml_declaration=True, encoding='UTF-8') if tree is not None else self.supplier_xml
        return self._create_invoice_from_xml(xml)

    def test_retrieve_partner_vals(self):
        # Realistic supplier: several identifications, no CompanyID, country given by its name.
        vals = self._supplier_vals()
        self.assertEqual(vals['vat'], SUPPLIER_VKN, "the VKN must win over the trade registry and MERSIS numbers")
        self.assertEqual(vals['name'], SUPPLIER_NAME)
        self.assertEqual(vals['postal_address']['country_code'], 'TR')
        # The VKN wins over a CompanyID as well.
        self.assertEqual(self._supplier_vals(company_id=OTHER_VKN)['vat'], SUPPLIER_VKN)
        # A natural person is identified by a TCKN.
        self.assertEqual(self._supplier_vals(identifications=[('TCKN', PERSON_TCKN)])['vat'], PERSON_TCKN)
        # Without a VKN/TCKN, a CompanyID is accepted when it is shaped like one...
        self.assertEqual(self._supplier_vals(identifications=[('MERSISNO', MERSIS_NUMBER)], company_id=SUPPLIER_VKN)['vat'], SUPPLIER_VKN)
        # ... but the trade registry and MERSIS numbers are not tax identifiers.
        self.assertFalse(self._supplier_vals(identifications=[('TICARETSICILNO', TRADE_REGISTRY_NUMBER)], company_id=TRADE_REGISTRY_NUMBER)['vat'])
        self.assertFalse(self._supplier_vals(identifications=[('MERSISNO', MERSIS_NUMBER)], company_id=MERSIS_NUMBER)['vat'])
        # An explicit country code is kept.
        self.assertEqual(self._supplier_vals(country_code='DE', country_name='Deutschland')['postal_address']['country_code'], 'DE')
        # A foreign country name without tax identifier: nothing is guessed.
        vals = self._supplier_vals(identifications=[('TICARETSICILNO', TRADE_REGISTRY_NUMBER)], country_name='Deutschland')
        self.assertFalse(vals['vat'])
        self.assertFalse(vals['postal_address'].get('country_code'))
        # A VKN without any country information means Türkiye.
        self.assertEqual(self._supplier_vals(country_name=None)['postal_address']['country_code'], 'TR')
        # The customer is read the same way (the fixture's customer carries a TCKN).
        customer_vals = self.env['account.edi.xml.ubl.tr']._import_retrieve_partner_vals(etree.fromstring(self.supplier_xml), 'AccountingCustomer')
        self.assertEqual(customer_vals['vat'], PERSON_TCKN)

    def test_import_matches_existing_partner_by_vat(self):
        existing = self.env['res.partner'].create({
            'name': 'Anadolu Elektrik Dagitim Anonim Sirketi',  # not the name written in the document
            'vat': SUPPLIER_VKN,
            'country_id': self.env.ref('base.tr').id,
            'is_company': True,
        })

        invoice = self._import()

        self.assertEqual(invoice.partner_id, existing)
        self.assertEqual(self.env['res.partner'].search_count([('vat', '=', SUPPLIER_VKN)]), 1, "no duplicate")
        chatter = ' '.join(str(message.body) for message in invoice.message_ids)
        self.assertNotIn('Could not retrieve partner', chatter)
        self.assertNotIn('A new partner was created', chatter)

    def test_import_creates_missing_partner(self):
        self.assertFalse(self.env['res.partner'].search([('vat', '=', SUPPLIER_VKN)]))

        invoice = self._import()

        self.assertRecordValues(invoice.partner_id, [{
            'name': SUPPLIER_NAME,
            'vat': SUPPLIER_VKN,
            'is_company': True,
            'country_id': self.env.ref('base.tr').id,
            'street': '3281. Cadde',
            'city': 'Düzce',
            'zip': '06810',
        }])

    def test_import_drops_invalid_vkn(self):
        self.ensure_installed('base_vat')

        invoice = self._import(self._supplier_tree(identifications=[('VKN', INVALID_VKN)]))

        # The partner is created because a name and a VAT were read, but the VAT does not pass the checksum.
        self.assertTrue(invoice.partner_id)
        self.assertFalse(invoice.partner_id.vat)
        self.assertEqual(invoice.partner_id.country_id.code, 'TR')
