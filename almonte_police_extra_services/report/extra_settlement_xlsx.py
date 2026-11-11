# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, models

COLUMNS = (
    ("DNI", 14),
    ("Funcionario", 36),
    ("Categoría", 20),
    ("Grupo", 14),
    ("Servicios", 11),
    ("Días realizados", 15),
    ("Euros totales liquidables (€)", 26),
)


class ExtraSettlementXlsx(models.AbstractModel):
    _name = "report.almonte_police_extra_services.extra_settlement_xlsx"
    _inherit = "report.report_xlsx.abstract"
    _description = "Liquidación de servicios extraordinarios (Excel)"

    def generate_xlsx_report(self, workbook, data, settlements):
        title_format = workbook.add_format({"bold": True, "font_size": 13})
        header_format = workbook.add_format(
            {"bold": True, "bg_color": "#EEF2F7", "border": 1, "text_wrap": True}
        )
        text_format = workbook.add_format({"border": 1})
        number_format = workbook.add_format({"border": 1, "num_format": "0.##"})
        amount_format = workbook.add_format({"border": 1, "num_format": "#,##0.00 €"})
        total_format = workbook.add_format(
            {"bold": True, "border": 1, "num_format": "#,##0.00 €"}
        )
        bold_format = workbook.add_format({"bold": True, "border": 1})
        for settlement in settlements:
            sheet = workbook.add_worksheet(
                f"{settlement.date_from:%Y-%m} {settlement.id}"[:31]
            )
            sheet.write(0, 0, settlement.name, title_format)
            sheet.write(
                1,
                0,
                _(
                    "Periodo: %(date_from)s - %(date_to)s",
                    date_from=settlement.date_from.strftime("%d/%m/%Y"),
                    date_to=settlement.date_to.strftime("%d/%m/%Y"),
                ),
            )
            for column, (header, width) in enumerate(COLUMNS):
                sheet.set_column(column, column, width)
                sheet.write(3, column, header, header_format)
            row = 4
            for line in settlement.line_ids:
                sheet.write(row, 0, line.identification_id or "", text_format)
                sheet.write(row, 1, line.employee_id.name, text_format)
                sheet.write(row, 2, line.police_category_id.name or "", text_format)
                sheet.write(row, 3, line.police_group_id.name or "", text_format)
                sheet.write_number(row, 4, line.service_count, number_format)
                sheet.write_number(row, 5, line.total_days, number_format)
                sheet.write_number(row, 6, line.total_amount, amount_format)
                row += 1
            sheet.write(row, 3, _("TOTAL"), bold_format)
            sheet.write_number(row, 4, settlement.total_services, bold_format)
            sheet.write_number(row, 5, settlement.total_days, bold_format)
            sheet.write_number(row, 6, settlement.total_amount, total_format)
            sheet.freeze_panes(4, 0)
