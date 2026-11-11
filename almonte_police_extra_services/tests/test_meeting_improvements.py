# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

import base64
import io
from datetime import date, timedelta

import openpyxl

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = "almonte_police_extra_services"


class TestAlmontePoliceMeetingImprovements(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.category = ref(f"{MODULE}.police_category_police")
        cls.officer_category = ref(f"{MODULE}.police_category_officer")
        cls.group_one = ref(f"{MODULE}.police_group_group_1")
        cls.morning = ref(f"{MODULE}.shift_morning")
        cls.afternoon = ref(f"{MODULE}.shift_afternoon")
        cls.rest = ref(f"{MODULE}.shift_rest")
        cls.vacation = ref(f"{MODULE}.shift_vacation")
        cls.sick_leave = ref(f"{MODULE}.shift_sick_leave")
        cls.union_hours = ref(f"{MODULE}.shift_union_hours")
        cls.medical_morning = ref(f"{MODULE}.shift_medical_morning")
        cls.service_type = cls.env["almonte.police.service.type"].create(
            {"name": "Servicio reunión", "code": "REU", "unit_price": 120}
        )
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.agent_user = users.create(
            {
                "name": "Agente Reunión",
                "login": "agente.reunion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_agent").id])],
            }
        )
        cls.other_agent_user = users.create(
            {
                "name": "Otro Agente Reunión",
                "login": "otro.agente.reunion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_agent").id])],
            }
        )
        cls.coordinator_user = users.create(
            {
                "name": "Coordinador Reunión",
                "login": "coordinador.reunion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_user").id])],
            }
        )
        cls.manager_user = users.create(
            {
                "name": "Responsable Reunión",
                "login": "responsable.reunion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_manager").id])],
            }
        )
        cls.employee = cls._employee("Agente Reunión", 9601, cls.agent_user)
        cls.other_employee = cls._employee(
            "Otro Agente Reunión", 9602, cls.other_agent_user
        )

    @classmethod
    def _employee(cls, name, number, user=None):
        return cls.env["hr.employee"].create(
            {
                "name": name,
                "police_employee_id": number,
                "police_dip": f"DIP-{number}",
                "police_category_id": cls.category.id,
                "user_id": user.id if user else False,
            }
        )

    def _schedule(self, schedule_date, shift, employee=None):
        return self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": (employee or self.employee).id,
                "date": schedule_date,
                "shift_type_id": shift.id,
            }
        )

    def _service(self, service_date, shift=None, vacancies=1):
        return self.env["almonte.police.extra.service"].create(
            {
                "service_date": service_date,
                "required_shift_id": (shift or self.morning).id,
                "service_type_id": self.service_type.id,
                "vacancies": vacancies,
            }
        )

    def _signup(self, service, employee=None, row_number=0):
        return self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": (employee or self.employee).id,
                "row_number": row_number,
            }
        )

    # Códigos alternativos (HRS) de los tipos de turno

    def test_union_hours_accepts_hrs_alias(self):
        code_map = self.env["almonte.police.shift.type"]._get_code_map()
        self.assertEqual(code_map["HS"], self.union_hours)
        self.assertEqual(code_map["HRS"], self.union_hours)
        self.assertEqual(code_map["CMM"], self.medical_morning)

    def test_alias_cannot_clash_with_other_code(self):
        with self.assertRaises(ValidationError):
            self.vacation.alias_codes = "hrs"
        with self.assertRaises(ValidationError):
            self.vacation.alias_codes = " m , X1"

    def test_alias_normalization_ignores_blanks(self):
        self.vacation.alias_codes = " vacaciones , ,"
        self.assertEqual(self.vacation._get_all_codes(), {"VAC", "VACACIONES"})

    # Ausencias parciales parametrizables (cita médica)

    def test_medical_morning_blocks_only_morning_extras(self):
        service_date = date(2027, 6, 7)
        self._schedule(service_date, self.medical_morning)
        morning_signup = self._signup(self._service(service_date, self.morning))
        afternoon_signup = self._signup(self._service(service_date, self.afternoon))
        self.assertEqual(morning_signup.priority_tier, "5_unavailable")
        self.assertIn("CMM", morning_signup.availability_status)
        self.assertEqual(afternoon_signup.priority_tier, "1_free")

    def test_absence_block_modes(self):
        self.assertTrue(self.vacation._blocks_extra_shift(self.afternoon))
        self.assertFalse(self.rest._blocks_extra_shift(self.afternoon))
        self.sick_leave.extra_block_mode = "none"
        self.assertFalse(self.sick_leave._blocks_extra_shift(self.morning))
        service_date = date(2027, 6, 8)
        self._schedule(service_date, self.sick_leave)
        signup = self._signup(self._service(service_date))
        self.assertEqual(signup.priority_tier, "1_free")

    # Descanso mínimo parametrizable

    def test_min_rest_hours_parameter_changes_priority(self):
        early = self.env["almonte.police.shift.type"].create(
            {
                "name": "Madrugada reunión",
                "code": "MADR",
                "state_kind": "work",
                "start_hour": 0,
                "end_hour": 6,
            }
        )
        service_date = date(2027, 6, 9)
        self._schedule(service_date, early)
        service = self._service(service_date, self.afternoon)
        signup = self._signup(service)
        self.assertEqual(signup.priority_tier, "2_rested")
        self.assertEqual(signup.availability_status, "8 H LIBRES ANTES")
        service.action_publish()
        wizard = (
            self.env["almonte.police.assignment.settings.wizard"]
            .with_user(self.manager_user)
            .create({"min_rest_hours": 10})
        )
        action = wizard.action_apply()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(self.env.company.police_min_rest_hours, 10)
        self.assertEqual(signup.priority_tier, "3_double")

    def test_settings_wizard_defaults_and_limits(self):
        wizard = self.env["almonte.police.assignment.settings.wizard"].create({})
        self.assertEqual(wizard.min_rest_hours, 8)
        with self.assertRaises(ValidationError):
            self.env.company.police_min_rest_hours = 49

    def test_settings_wizard_is_manager_only(self):
        with self.assertRaises(AccessError):
            self.env["almonte.police.assignment.settings.wizard"].with_user(
                self.coordinator_user
            ).create({"min_rest_hours": 6})

    # Reapertura de ofertas para ampliar plazas

    def test_reopen_keeps_assignments_and_assigns_new_vacancies(self):
        service_date = date(2027, 6, 14)
        third_employee = self._employee("Tercer Agente Reunión", 9603)
        for employee in (self.employee, self.other_employee, third_employee):
            self._schedule(service_date, self.rest, employee)
        service = self._service(service_date)
        first = self._signup(service, self.employee, 1)
        second = self._signup(service, self.other_employee, 2)
        service.action_publish()
        service.action_assign()
        self.assertEqual(first.state, "assigned_free")
        self.assertEqual(second.state, "waiting_free")
        service.action_reopen()
        self.assertEqual(service.state, "published")
        self.assertEqual(first.state, "assigned_free")
        self.assertEqual(second.state, "eligible_free")
        late = self._signup(service, third_employee, 3)
        service.vacancies = 2
        service.action_assign()
        self.assertEqual(first.state, "assigned_free")
        self.assertEqual(second.state, "assigned_free")
        self.assertEqual(late.state, "waiting_free")
        self.assertEqual(len(service.history_ids), 2)

    def test_reopen_only_from_assigned_and_clears_expired_deadline(self):
        service_date = date(2027, 6, 15)
        self._schedule(service_date, self.rest)
        service = self._service(service_date)
        with self.assertRaises(UserError):
            service.action_reopen()
        self._signup(service)
        service.action_publish()
        service.action_assign()
        service.signup_deadline = "2020-01-01 10:00:00"
        service.action_reopen()
        self.assertFalse(service.signup_deadline)
        self.assertTrue(service.is_signup_open)
        self.assertIn("vencido", service.message_ids[0].body)

    def test_vacancies_cannot_drop_below_assigned(self):
        service_date = date(2027, 6, 16)
        self._schedule(service_date, self.rest)
        self._schedule(service_date, self.rest, self.other_employee)
        service = self._service(service_date, vacancies=2)
        self._signup(service, self.employee, 1)
        self._signup(service, self.other_employee, 2)
        service.action_publish()
        service.action_assign()
        with self.assertRaises(ValidationError):
            service.vacancies = 1

    # Solicitudes de ausencia del agente

    def _request(self, user=None, **values):
        vals = {
            "shift_type_id": self.vacation.id,
            "date_from": date(2027, 7, 1),
            "date_to": date(2027, 7, 3),
        }
        vals.update(values)
        model = self.env["almonte.police.absence.request"]
        return model.with_user(user or self.agent_user).create(vals)

    def test_agent_request_approved_writes_schedule(self):
        existing = self._schedule(date(2027, 7, 2), self.morning)
        request = self._request(reason="Vacaciones de verano", response="Hack")
        self.assertEqual(request.employee_id, self.employee)
        self.assertEqual(request.state, "pending")
        self.assertEqual(request.day_count, 3)
        self.assertFalse(request.response)
        self.assertIn(self.vacation.name, request.name)
        request.with_user(self.coordinator_user).action_approve()
        self.assertEqual(request.state, "approved")
        assignments = self.env["almonte.police.schedule.assignment"].search(
            [
                ("employee_id", "=", self.employee.id),
                ("date", ">=", date(2027, 7, 1)),
                ("date", "<=", date(2027, 7, 3)),
            ]
        )
        self.assertEqual(len(assignments), 3)
        self.assertEqual(assignments.shift_type_id, self.vacation)
        self.assertIn(existing, assignments)
        self.assertIn(self.agent_user.partner_id, request.message_ids.partner_ids)

    def test_agent_request_refused_and_cancel(self):
        refused = self._request()
        refused.with_user(self.coordinator_user).action_refuse()
        self.assertEqual(refused.state, "refused")
        with self.assertRaises(UserError):
            refused.with_user(self.coordinator_user).action_approve()
        with self.assertRaises(UserError):
            refused.with_user(self.agent_user).action_cancel()
        cancelled = self._request(date_from=date(2027, 8, 1), date_to=date(2027, 8, 1))
        cancelled.with_user(self.agent_user).action_cancel()
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(cancelled.day_count, 1)
        self.assertNotIn("-", cancelled.name.split("·")[-1])

    def test_agent_request_permissions(self):
        request = self._request()
        with self.assertRaises(AccessError):
            request.with_user(self.agent_user).action_approve()
        with self.assertRaises(AccessError):
            request.with_user(self.other_agent_user).action_cancel()
        with self.assertRaises(AccessError):
            self._request(employee_id=self.other_employee.id)
        with self.assertRaises(AccessError):
            self._request(state="approved")
        visible = (
            self.env["almonte.police.absence.request"]
            .with_user(self.other_agent_user)
            .search([])
        )
        self.assertNotIn(request, visible)
        coordinator_request = self._request(
            self.coordinator_user, employee_id=self.other_employee.id
        )
        self.assertEqual(coordinator_request.employee_id, self.other_employee)

    def test_agent_request_validations(self):
        with self.assertRaises(ValidationError):
            self._request(date_from=date(2027, 7, 5), date_to=date(2027, 7, 1))
        with self.assertRaises(ValidationError):
            self._request(date_from=date(2027, 1, 1), date_to=date(2028, 6, 1))
        with self.assertRaises(ValidationError):
            self._request(shift_type_id=self.sick_leave.id)
        unlinked_user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Agente sin ficha",
                    "login": "agente.sin.ficha@example.com",
                    "group_ids": [
                        (6, 0, [self.env.ref(f"{MODULE}.group_police_extra_agent").id])
                    ],
                }
            )
        )
        with self.assertRaises(UserError):
            self._request(unlinked_user)

    def test_new_request_name_without_data(self):
        request = self.env["almonte.police.absence.request"].new({})
        self.assertEqual(request.name, "Nueva solicitud")
        self.assertEqual(request.day_count, 0)

    # Login con DIP y regeneración de contraseña

    def test_create_user_uses_dip_without_email(self):
        employee = self._employee("Agente Sin Correo", 9610)
        action = employee.with_user(self.manager_user).action_create_police_user()
        self.assertEqual(employee.user_id.login, "DIP-9610")
        self.assertIn("DIP-9610", action["params"]["message"])
        self.assertTrue(action["params"]["sticky"])
        with self.assertRaises(UserError):
            employee.with_user(self.manager_user).action_create_police_user()

    def test_create_user_requires_dip_and_category(self):
        employee = self._employee("Agente Sin DIP", 9611)
        employee.police_dip = False
        with self.assertRaises(UserError):
            employee.with_user(self.manager_user).action_create_police_user()
        employee.write({"police_dip": "DIP-9611", "police_category_id": False})
        with self.assertRaises(UserError):
            employee.with_user(self.manager_user).action_create_police_user()

    def test_create_user_rejects_duplicated_login(self):
        employee = self._employee("Agente Login Repetido", 9612)
        employee.police_dip = self.agent_user.login
        with self.assertRaises(UserError):
            employee.with_user(self.manager_user).action_create_police_user()

    def test_regenerate_password(self):
        action = self.employee.with_user(
            self.manager_user
        ).action_regenerate_police_password()
        self.assertEqual(self.agent_user.login, "DIP-9601")
        self.assertEqual(action["params"]["title"], "Contraseña regenerada")
        with self.assertRaises(AccessError):
            self.employee.with_user(
                self.coordinator_user
            ).action_regenerate_police_password()
        without_user = self._employee("Agente Sin Usuario", 9613)
        with self.assertRaises(UserError):
            without_user.with_user(
                self.manager_user
            ).action_regenerate_police_password()
        self.employee.police_dip = False
        with self.assertRaises(UserError):
            self.employee.with_user(
                self.manager_user
            ).action_regenerate_police_password()

    def test_generated_password_alphabet(self):
        password = self.env["hr.employee"]._generate_police_password()
        self.assertEqual(len(password), 10)
        self.assertFalse(set(password) & set("0OoIl1"))

    # Importador de cuadrantes desde Excel

    def _workbook(self, employees=None, schedule=None, dates=None):
        workbook = openpyxl.Workbook()
        employee_sheet = workbook.active
        employee_sheet.title = "Policías"
        employee_sheet.append(["PLANTILLA"])
        employee_sheet.append([])
        employee_sheet.append([])
        employee_sheet.append(
            ["ID Empleado", "Nombre y Apellidos", "Categoría", "DIP", "Grupo"]
        )
        for row in employees or []:
            employee_sheet.append(row)
        schedule_sheet = workbook.create_sheet("Cuadrante")
        schedule_sheet.append(["CUADRANTE"])
        schedule_sheet.append(["M Mañana", "T Tarde"])
        schedule_sheet.append([])
        dates = dates or ["01/08/2027", "02/08/2027", "03/08/2027"]
        schedule_sheet.append(["ID", "Empleado", "Categoría", "Grupo", *dates])
        for row in schedule or []:
            schedule_sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        return base64.b64encode(stream.getvalue())

    def _import(self, file_content, user=None, import_employees=True):
        wizard = (
            self.env["almonte.police.schedule.import.wizard"]
            .with_user(user or self.coordinator_user)
            .create(
                {
                    "file": file_content,
                    "filename": "SERVICIOS.xlsx",
                    "import_employees": import_employees,
                }
            )
        )
        return wizard.action_import()

    def _day(self, employee, schedule_date):
        return self.env["almonte.police.schedule.assignment"].get_for_employee_date(
            employee, schedule_date
        )

    def test_import_creates_agents_and_schedule(self):
        self._schedule(date(2027, 8, 1), self.morning)
        content = self._workbook(
            employees=[
                [9601, "Nombre que no se pisa ", "Oficial Policía", 9601, "Grupo 1"],
                [9620, "Agente Importado ", "policia", 9620, None],
                [None, None, None, None, None],
            ],
            schedule=[
                [9601, "Agente Reunión", "", "", "T", "hrs", None],
                [9620, "Agente Importado", "", "", "D", "N", "VAC"],
            ],
            dates=["01/08/2027", date(2027, 8, 2), "03/08/2027"],
        )
        action = self._import(content)
        self.assertEqual(action["params"]["type"], "success")
        self.assertIn("1 nuevos y 1 actualizados", action["params"]["message"])
        self.assertIn("4 días creados y 1 actualizados", action["params"]["message"])
        self.assertEqual(self.employee.name, "Agente Reunión")
        self.assertEqual(self.employee.police_category_id, self.officer_category)
        self.assertEqual(self.employee.police_group_id, self.group_one)
        self.assertEqual(self.employee.police_dip, "9601")
        imported = self.env["hr.employee"].search([("police_employee_id", "=", 9620)])
        self.assertEqual(imported.name, "Agente Importado")
        self.assertEqual(imported.police_category_id, self.category)
        self.assertFalse(imported.police_group_id)
        self.assertEqual(
            self._day(self.employee, date(2027, 8, 1)).shift_type_id, self.afternoon
        )
        self.assertEqual(
            self._day(self.employee, date(2027, 8, 2)).shift_type_id,
            self.union_hours,
        )
        self.assertFalse(self._day(self.employee, date(2027, 8, 3)))
        self.assertEqual(
            self._day(imported, date(2027, 8, 3)).shift_type_id, self.vacation
        )

    def test_import_schedule_only_and_unchanged_days(self):
        self._schedule(date(2027, 8, 1), self.morning)
        content = self._workbook(schedule=[[9601, "", "", "", "M", "", ""]])
        action = self._import(content, import_employees=False)
        self.assertIn("0 días creados y 0 actualizados", action["params"]["message"])

    def test_import_errors_import_nothing(self):
        content = self._workbook(
            employees=[
                [9630, "Agente Error", "Comisario", 9630, "Grupo 99"],
                [9631, "", "Policía", 9631, None],
                [9632, "Agente DIP", "Policía", "DIP-9601", None],
                [9633, "Agente Repetido", "Policía", 9633, None],
                [9633, "Agente Repetido", "Policía", 9634, None],
                ["X1", "Agente ID", "Policía", 9635, None],
            ],
            schedule=[
                [9601, "", "", "", "ZZ", "M", "M"],
                [9999, "Desconocido", "", "", "M", "M", "M"],
                ["abc", "", "", "", "M", "M", "M"],
            ],
        )
        with self.assertRaises(UserError) as error:
            self._import(content)
        message = str(error.exception)
        for expected in (
            "categoría desconocida 'Comisario'",
            "grupo desconocido 'Grupo 99'",
            "falta el nombre",
            "ya pertenece a otro agente",
            "está repetido",
            "ID de empleado no válido 'X1'",
            "código de turno desconocido 'ZZ'",
            "no existe el agente con ID 9999",
        ):
            self.assertIn(expected, message)
        self.assertFalse(
            self.env["hr.employee"].search([("police_employee_id", "=", 9630)])
        )
        self.assertFalse(self._day(self.employee, date(2027, 8, 2)))

    def test_import_truncates_long_error_lists(self):
        content = self._workbook(
            schedule=[[9601, "", "", "", "ZZ", "ZZ", "ZZ"]] * 12,
        )
        with self.assertRaises(UserError) as error:
            self._import(content, import_employees=False)
        self.assertIn("6 errores más", str(error.exception))

    def test_import_structure_errors(self):
        with self.assertRaises(UserError):
            self._import(base64.b64encode(b"esto no es un excel"))
        workbook = openpyxl.Workbook()
        workbook.active.title = "Otra"
        stream = io.BytesIO()
        workbook.save(stream)
        with self.assertRaises(UserError) as error:
            self._import(base64.b64encode(stream.getvalue()))
        self.assertIn("'Policías'", str(error.exception))
        self.assertIn("'Cuadrante'", str(error.exception))
        workbook = openpyxl.Workbook()
        workbook.active.title = "Policías"
        workbook.active.append(["Sin cabecera"])
        workbook.create_sheet("Cuadrante").append(["Sin cabecera"])
        stream = io.BytesIO()
        workbook.save(stream)
        with self.assertRaises(UserError) as error:
            self._import(base64.b64encode(stream.getvalue()))
        self.assertIn("cabecera 'ID Empleado'", str(error.exception))
        self.assertIn("cabecera 'ID'", str(error.exception))
        content = self._workbook(dates=["sin fecha"])
        with self.assertRaises(UserError) as error:
            self._import(content, import_employees=False)
        self.assertIn("no hay columnas con fechas", str(error.exception))

    def test_import_wizard_not_available_to_agents(self):
        with self.assertRaises(AccessError):
            self._import(self._workbook(), user=self.agent_user)

    def test_import_real_like_file_dates_sequence(self):
        dates = [
            (date(2027, 9, 1) + timedelta(days=offset)).strftime("%d/%m/%Y")
            for offset in range(5)
        ]
        content = self._workbook(
            schedule=[[9602, "", "", "", "M", "T", "N", "D", "L"]], dates=dates
        )
        self._import(content, import_employees=False)
        days = self.env["almonte.police.schedule.assignment"].search(
            [("employee_id", "=", self.other_employee.id)]
        )
        self.assertEqual(len(days), 5)
