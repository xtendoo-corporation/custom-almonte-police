# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

import base64
import io
import unicodedata
from datetime import date, datetime

from odoo import _, fields, models
from odoo.exceptions import UserError

MAX_REPORTED_ERRORS = 30
HEADER_SCAN_ROWS = 15
EMPLOYEE_SHEET = "POLICIAS"
SCHEDULE_SHEET = "CUADRANTE"
EMPLOYEE_COLUMNS = {
    "id": "ID EMPLEADO",
    "name": "NOMBRE Y APELLIDOS",
    "category": "CATEGORIA",
    "dip": "DIP",
    "group": "GRUPO",
}


def normalize(value):
    """Texto en mayúsculas, sin acentos ni espacios sobrantes."""
    text = unicodedata.normalize("NFKD", str(value if value is not None else ""))
    return (
        "".join(char for char in text if not unicodedata.combining(char))
        .strip()
        .upper()
    )


def cell_to_text(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip() if value is not None else ""


def cell_to_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        day, month, year = map(int, cell_to_text(value).split("/"))
        return date(year, month, day)
    except ValueError:
        return None


class PoliceScheduleImportWizard(models.TransientModel):
    _name = "almonte.police.schedule.import.wizard"
    _description = "Importar cuadrante y plantilla desde Excel"

    file = fields.Binary(string="Fichero Excel", required=True)
    filename = fields.Char(string="Nombre del fichero")
    import_employees = fields.Boolean(
        string="Importar plantilla (hoja Policías)",
        default=True,
        help="Da de alta los agentes nuevos y actualiza la categoría, el DIP "
        "y el grupo de los existentes, identificados por su ID de empleado.",
    )

    def _load_workbook(self):
        import openpyxl

        try:
            return openpyxl.load_workbook(
                io.BytesIO(base64.b64decode(self.file)),
                read_only=True,
                data_only=True,
            )
        except Exception as error:
            raise UserError(
                _("No se ha podido leer el fichero Excel: %s", error)
            ) from error

    @staticmethod
    def _get_sheet(workbook, sheet_name):
        for sheet in workbook.worksheets:
            if normalize(sheet.title) == sheet_name:
                return sheet
        return None

    @staticmethod
    def _find_header(rows, predicate):
        for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
            if predicate([normalize(cell) for cell in row]):
                return index
        return None

    def _parse_employees(self, sheet, errors):
        rows = list(sheet.iter_rows(values_only=True))
        header_index = self._find_header(
            rows, lambda cells: EMPLOYEE_COLUMNS["id"] in cells
        )
        if header_index is None:
            errors.append(
                _("Hoja Policías: no se encuentra la cabecera 'ID Empleado'.")
            )
            return []
        header = [normalize(cell) for cell in rows[header_index]]
        positions = {
            key: header.index(title)
            for key, title in EMPLOYEE_COLUMNS.items()
            if title in header
        }
        categories = {
            normalize(category.name): category
            for category in self.env["almonte.police.category"].search([])
        }
        groups = {
            normalize(group.name): group
            for group in self.env["almonte.police.group"].search([])
        }
        parsed = []
        for row_number, row in enumerate(rows[header_index + 1 :], header_index + 2):

            def value(key, row=row):
                position = positions.get(key)
                return cell_to_text(row[position]) if position is not None else ""

            employee_number = self._parse_employee_number(
                value("id"), _("Policías"), row_number, errors
            )
            if employee_number is None:
                continue
            category_name = value("category")
            category = categories.get(normalize(category_name))
            if category_name and not category:
                errors.append(
                    _(
                        "Policías fila %(row)s: categoría desconocida '%(name)s'.",
                        row=row_number,
                        name=category_name,
                    )
                )
            group_name = value("group")
            group = groups.get(normalize(group_name)) if group_name else None
            if group_name and not group:
                errors.append(
                    _(
                        "Policías fila %(row)s: grupo desconocido '%(name)s'.",
                        row=row_number,
                        name=group_name,
                    )
                )
            parsed.append(
                {
                    "row": row_number,
                    "police_employee_id": employee_number,
                    "name": value("name"),
                    "police_dip": value("dip"),
                    "police_category_id": category.id if category else False,
                    "police_group_id": group.id if group else False,
                }
            )
        return parsed

    @staticmethod
    def _parse_employee_number(raw_value, sheet_label, row_number, errors):
        if not raw_value:
            return None
        try:
            return int(float(raw_value))
        except ValueError:
            errors.append(
                _(
                    "%(sheet)s fila %(row)s: ID de empleado no válido '%(value)s'.",
                    sheet=sheet_label,
                    row=row_number,
                    value=raw_value,
                )
            )
            return None

    def _parse_schedule(self, sheet, known_numbers, errors):
        rows = list(sheet.iter_rows(values_only=True))
        header_index = self._find_header(
            rows, lambda cells: bool(cells) and cells[0] == "ID"
        )
        if header_index is None:
            errors.append(_("Hoja Cuadrante: no se encuentra la cabecera 'ID'."))
            return {}
        date_columns = {
            position: cell_to_date(cell)
            for position, cell in enumerate(rows[header_index])
            if position and cell_to_date(cell)
        }
        if not date_columns:
            errors.append(_("Hoja Cuadrante: no hay columnas con fechas."))
            return {}
        code_map = self.env["almonte.police.shift.type"]._get_code_map()
        schedule = {}
        for row_number, row in enumerate(rows[header_index + 1 :], header_index + 2):
            employee_number = self._parse_employee_number(
                cell_to_text(row[0]) if row else "",
                _("Cuadrante"),
                row_number,
                errors,
            )
            if employee_number is None:
                continue
            if employee_number not in known_numbers:
                errors.append(
                    _(
                        "Cuadrante fila %(row)s: no existe el agente con ID %(id)s.",
                        row=row_number,
                        id=employee_number,
                    )
                )
                continue
            for position, schedule_date in date_columns.items():
                code = normalize(row[position]) if position < len(row) else ""
                if not code:
                    continue
                shift = code_map.get(code)
                if not shift:
                    errors.append(
                        _(
                            "Cuadrante fila %(row)s, %(date)s: código de turno "
                            "desconocido '%(code)s'.",
                            row=row_number,
                            date=schedule_date.strftime("%d/%m/%Y"),
                            code=code,
                        )
                    )
                    continue
                schedule[(employee_number, schedule_date)] = shift
        return schedule

    def _raise_errors(self, errors):
        if not errors:
            return
        shown = errors[:MAX_REPORTED_ERRORS]
        message = "\n".join(shown)
        if len(errors) > MAX_REPORTED_ERRORS:
            message += "\n" + _(
                "... y %s errores más.", len(errors) - MAX_REPORTED_ERRORS
            )
        raise UserError(_("No se ha importado nada. Corrige el Excel:\n%s", message))

    def _check_employee_rows(self, employee_rows, employees_by_number, errors):
        dip_owner = {
            employee.police_dip: employee.police_employee_id
            for employee in self.env["hr.employee"]
            .with_context(active_test=False)
            .search([("police_dip", "!=", False)])
        }
        seen_numbers = set()
        for values in employee_rows:
            number = values["police_employee_id"]
            if number in seen_numbers:
                errors.append(
                    _(
                        "Policías fila %(row)s: el ID %(id)s está repetido.",
                        row=values["row"],
                        id=number,
                    )
                )
            seen_numbers.add(number)
            if number not in employees_by_number and not values["name"]:
                errors.append(
                    _(
                        "Policías fila %(row)s: falta el nombre del agente nuevo.",
                        row=values["row"],
                    )
                )
            dip = values["police_dip"]
            if dip and dip_owner.get(dip, number) != number:
                errors.append(
                    _(
                        "Policías fila %(row)s: el DIP %(dip)s ya pertenece a "
                        "otro agente.",
                        row=values["row"],
                        dip=dip,
                    )
                )
            if dip:
                dip_owner[dip] = number

    def _write_employees(self, employee_rows, employees_by_number):
        created = updated = 0
        # Los coordinadores no pueden crear empleados por ACL; el alta de
        # agentes desde el Excel oficial se hace de forma controlada con sudo.
        employee_model = self.env["hr.employee"].sudo()
        for values in employee_rows:
            vals = {
                key: values[key]
                for key in ("police_category_id", "police_group_id", "police_dip")
                if values[key] or key == "police_group_id"
            }
            employee = employees_by_number.get(values["police_employee_id"])
            if employee:
                employee.write(vals)
                updated += 1
            else:
                employees_by_number[values["police_employee_id"]] = (
                    employee_model.create(
                        dict(
                            vals,
                            name=values["name"],
                            police_employee_id=values["police_employee_id"],
                        )
                    ).sudo(False)
                )
                created += 1
        return created, updated

    def _write_schedule(self, schedule, employees_by_number):
        assignment_model = self.env["almonte.police.schedule.assignment"]
        if not schedule:
            return 0, 0
        employees = self.env["hr.employee"].union(
            *(employees_by_number[number] for number, _date in schedule)
        )
        dates = [schedule_date for _number, schedule_date in schedule]
        existing = {
            (assignment.employee_id.id, assignment.date): assignment
            for assignment in assignment_model.search(
                [
                    ("employee_id", "in", employees.ids),
                    ("date", ">=", min(dates)),
                    ("date", "<=", max(dates)),
                ]
            )
        }
        to_create = []
        to_update = {}
        for (number, schedule_date), shift in schedule.items():
            employee = employees_by_number[number]
            assignment = existing.get((employee.id, schedule_date))
            if not assignment:
                to_create.append(
                    {
                        "employee_id": employee.id,
                        "date": schedule_date,
                        "shift_type_id": shift.id,
                    }
                )
            elif assignment.shift_type_id != shift:
                to_update[shift] = to_update.get(shift, assignment_model) | assignment
        assignment_model.create(to_create)
        for shift, assignments in to_update.items():
            assignments.write({"shift_type_id": shift.id})
        return len(to_create), sum(len(records) for records in to_update.values())

    def action_import(self):
        self.ensure_one()
        workbook = self._load_workbook()
        errors = []
        employees_by_number = {
            employee.police_employee_id: employee
            for employee in self.env["hr.employee"]
            .with_context(active_test=False)
            .search([("police_employee_id", ">", 0)])
        }
        employee_rows = []
        if self.import_employees:
            employee_sheet = self._get_sheet(workbook, EMPLOYEE_SHEET)
            if employee_sheet is None:
                errors.append(_("No se encuentra la hoja 'Policías'."))
            else:
                employee_rows = self._parse_employees(employee_sheet, errors)
                self._check_employee_rows(employee_rows, employees_by_number, errors)
        schedule_sheet = self._get_sheet(workbook, SCHEDULE_SHEET)
        if schedule_sheet is None:
            errors.append(_("No se encuentra la hoja 'Cuadrante'."))
            schedule = {}
        else:
            known_numbers = set(employees_by_number) | {
                values["police_employee_id"] for values in employee_rows
            }
            schedule = self._parse_schedule(schedule_sheet, known_numbers, errors)
        self._raise_errors(errors)
        created_employees, updated_employees = self._write_employees(
            employee_rows, employees_by_number
        )
        created_days, updated_days = self._write_schedule(schedule, employees_by_number)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Importación completada"),
                "message": _(
                    "Agentes: %(created_employees)s nuevos y %(updated_employees)s "
                    "actualizados. Cuadrante: %(created_days)s días creados y "
                    "%(updated_days)s actualizados.",
                    created_employees=created_employees,
                    updated_employees=updated_employees,
                    created_days=created_days,
                    updated_days=updated_days,
                ),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
