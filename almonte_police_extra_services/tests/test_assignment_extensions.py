# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestAlmontePoliceAssignmentExtensions(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env.ref(
            "almonte_police_extra_services.police_category_police"
        )
        cls.employee = cls._employee("Agente Extensiones", 9501)
        cls.morning = cls.env.ref("almonte_police_extra_services.shift_morning")
        cls.afternoon = cls.env.ref("almonte_police_extra_services.shift_afternoon")
        cls.night = cls.env.ref("almonte_police_extra_services.shift_night")
        cls.rest = cls.env.ref("almonte_police_extra_services.shift_rest")
        cls.vacation = cls.env.ref("almonte_police_extra_services.shift_vacation")
        cls.service_type = cls.env["almonte.police.service.type"].create(
            {"name": "Servicio extensiones", "code": "EXT", "unit_price": 150}
        )
        cls.history_model = cls.env["almonte.police.extra.service.history"]

    @classmethod
    def _employee(cls, name, number):
        return cls.env["hr.employee"].create(
            {
                "name": name,
                "police_employee_id": number,
                "police_dip": f"DIP-{number}",
                "police_category_id": cls.category.id,
            }
        )

    def _schedule(self, service_date, shift, employee=None):
        return self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": (employee or self.employee).id,
                "date": service_date,
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

    def _assigned_service(self, service_date, shift=None, employee=None):
        employee = employee or self.employee
        schedule_model = self.env["almonte.police.schedule.assignment"]
        if not schedule_model.get_for_employee_date(employee, service_date):
            self._schedule(service_date, self.rest, employee)
        service = self._service(service_date, shift)
        self._signup(service, employee)
        service.action_publish()
        service.action_assign()
        self.assertTrue(
            service.history_ids.filtered(lambda h: h.employee_id == employee)
        )
        return service

    def _backlog(self, service_date, days, amount, employee=None):
        return self.history_model.create(
            {
                "origin": "backlog",
                "employee_id": (employee or self.employee).id,
                "service_date": service_date,
                "days": days,
                "amount": amount,
            }
        )

    # 1. Exclusión de agentes ya adjudicados en la misma franja

    def test_agent_assigned_same_day_and_shift_is_restricted(self):
        service_date = date(2027, 5, 10)
        self._schedule(service_date, self.rest)
        first = self._assigned_service(service_date)
        second = self._service(service_date)
        signup = self._signup(second)
        self.assertEqual(signup.priority_tier, "4_restricted")
        self.assertEqual(signup.state, "restricted")
        self.assertIn(first.name, signup.availability_status)
        second.action_publish()
        second.action_assign()
        self.assertEqual(signup.state, "restricted")
        self.assertFalse(second.history_ids)

    def test_overlap_with_previous_day_service_is_restricted(self):
        service_date = date(2027, 5, 12)
        self._schedule(service_date - timedelta(days=1), self.rest)
        self._schedule(service_date, self.rest)
        early = self.env["almonte.police.shift.type"].create(
            {
                "name": "Madrugada",
                "code": "MAD",
                "state_kind": "work",
                "start_hour": 5,
                "end_hour": 9,
            }
        )
        night_service = self._assigned_service(
            service_date - timedelta(days=1), self.night
        )
        signup = self._signup(self._service(service_date, early))
        self.assertEqual(signup.priority_tier, "4_restricted")
        self.assertIn(night_service.name, signup.availability_status)

    def test_non_overlapping_service_same_day_is_allowed(self):
        service_date = date(2027, 5, 14)
        self._schedule(service_date, self.rest)
        self._assigned_service(service_date, self.morning)
        signup = self._signup(self._service(service_date, self.afternoon))
        self.assertEqual(signup.priority_tier, "1_free")

    def test_backlog_does_not_block_same_day_service(self):
        service_date = date(2027, 5, 16)
        self._schedule(service_date, self.rest)
        self._backlog(service_date, 1, 150)
        signup = self._signup(self._service(service_date))
        self.assertEqual(signup.priority_tier, "1_free")

    def test_conflict_is_a_signup_block_reason(self):
        service_date = date(2027, 5, 18)
        self._schedule(service_date, self.rest)
        first = self._assigned_service(service_date)
        second = self._service(service_date)
        second.action_publish()
        reason = second._get_signup_block_reason(self.employee)
        self.assertIn(first.name, reason)
        self.assertFalse(
            self._service(service_date, self.afternoon)._get_conflicting_history(
                self.employee
            )
        )

    # 2. Puesta a cero del baremo a fecha

    def test_baremo_reset_date_excludes_older_history(self):
        self._assigned_service(date(2026, 7, 1))
        self._assigned_service(date(2026, 8, 20))
        service = self._service(date(2026, 9, 24))
        self.assertEqual(self._signup(service).quarter_service_count, 2)
        wizard = self.env["almonte.police.baremo.reset.wizard"].create(
            {"reset_date": date(2026, 8, 1)}
        )
        action = wizard.action_apply()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(self.env.company.police_baremo_reset_date, date(2026, 8, 1))
        other_service = self._service(date(2026, 9, 25))
        self.assertEqual(self._signup(other_service).quarter_service_count, 1)
        wizard = self.env["almonte.police.baremo.reset.wizard"].create({})
        self.assertEqual(wizard.current_reset_date, date(2026, 8, 1))
        wizard.action_clear()
        self.assertFalse(self.env.company.police_baremo_reset_date)
        third_service = self._service(date(2026, 9, 26))
        self.assertEqual(self._signup(third_service).quarter_service_count, 2)
        self.assertEqual(
            len(self.history_model.search([("employee_id", "=", self.employee.id)])), 2
        )

    # 3. Extras atrasados

    def test_backlog_days_count_for_baremo(self):
        self._assigned_service(date(2026, 7, 2))
        self._backlog(date(2026, 7, 10), 3, 600)
        self._backlog(date(2026, 3, 10), 5, 1000)
        service = self._service(date(2026, 9, 24))
        self.assertEqual(self._signup(service).quarter_service_count, 4)

    def test_backlog_lowers_priority_against_equal_agent(self):
        service_date = date(2027, 2, 10)
        rival = self._employee("Agente sin atrasos", 9502)
        self._schedule(service_date, self.rest)
        self._schedule(service_date, self.rest, rival)
        self._backlog(date(2027, 1, 5), 2, 400)
        service = self._service(service_date)
        mine = self._signup(service, row_number=1)
        theirs = self._signup(service, rival, row_number=2)
        service.action_publish()
        service.action_assign()
        self.assertEqual(theirs.state, "assigned_free")
        self.assertEqual(mine.state, "waiting_free")

    def test_backlog_is_included_in_settlement_and_summary(self):
        backlog = self._backlog(date(2027, 3, 8), 2, 400)
        self.assertEqual(backlog.currency_id, self.env.company.currency_id)
        settlement = self.env["almonte.police.extra.settlement"].create(
            {"period_type": "month", "year": 2027, "month": "3", "scope": "all"}
        )
        settlement.action_generate()
        line = settlement.line_ids.filtered(
            lambda item: item.employee_id == self.employee
        )
        self.assertEqual(line.total_days, 2)
        self.assertEqual(line.total_amount, 400)
        self.env.flush_all()
        summary = self.env["almonte.police.extra.summary"].search(
            [("employee_id", "=", self.employee.id)]
        )
        self.assertEqual(summary.total_amount, 400)

    def test_backlog_and_origin_validations(self):
        with self.assertRaises(ValidationError):
            self._backlog(date(2027, 3, 1), 0, 100)
        with self.assertRaises(ValidationError):
            self._backlog(date(2027, 3, 1), 1, -5)
        with self.assertRaises(ValidationError):
            self.history_model.create(
                {
                    "employee_id": self.employee.id,
                    "service_date": date(2027, 3, 1),
                    "amount": 100,
                }
            )

    # 4. Generador de cuadrante por ciclos

    def _generate(self, **values):
        defaults = {
            "employee_ids": [(6, 0, self.employee.ids)],
            "date_start": date(2027, 6, 1),
            "scope": "month",
        }
        wizard = self.env["almonte.police.schedule.generate.wizard"].create(
            dict(defaults, **values)
        )
        return wizard, wizard.action_generate()

    def _codes(self, start, days, employee=None):
        assignments = self.env["almonte.police.schedule.assignment"].search(
            [
                ("employee_id", "=", (employee or self.employee).id),
                ("date", ">=", start),
                ("date", "<", start + timedelta(days=days)),
            ],
            order="date",
        )
        return [assignment.shift_type_id.code for assignment in assignments]

    def test_generate_6_5_cycle(self):
        wizard, action = self._generate(pattern="6_5")
        self.assertEqual(wizard.date_end, date(2027, 6, 30))
        self.assertIn("30 días creados", action["params"]["message"])
        self.assertEqual(
            self._codes(date(2027, 6, 1), 13),
            ["M", "M", "T", "T", "N", "N", "D", "D", "D", "D", "D", "M", "M"],
        )

    def test_generate_cycle_with_start_phase(self):
        self._generate(pattern="6_5", cycle_day=6)
        self.assertEqual(
            self._codes(date(2027, 6, 1), 7), ["N", "D", "D", "D", "D", "D", "M"]
        )

    def test_generate_5_2_follows_weekdays(self):
        self._generate(pattern="5_2", date_start=date(2027, 6, 3))
        # 3 de junio de 2027 es jueves.
        self.assertEqual(
            self._codes(date(2027, 6, 3), 7), ["M", "M", "D", "D", "M", "M", "M"]
        )

    def test_generate_7_7_cycle(self):
        self._generate(pattern="7_7")
        self.assertEqual(
            self._codes(date(2027, 6, 1), 15), ["M"] * 7 + ["D"] * 7 + ["M"]
        )

    def test_generate_preserves_absences_and_updates_other_days(self):
        self._schedule(date(2027, 6, 1), self.vacation)
        self._schedule(date(2027, 6, 2), self.night)
        _wizard, action = self._generate(pattern="7_7")
        self.assertIn("1 actualizados", action["params"]["message"])
        self.assertIn("1 ausencias conservadas", action["params"]["message"])
        self.assertEqual(self._codes(date(2027, 6, 1), 2), ["VAC", "M"])
        self._generate(pattern="7_7", preserve_absences=False)
        self.assertEqual(self._codes(date(2027, 6, 1), 1), ["M"])

    def test_generate_scopes_and_validation(self):
        wizard_model = self.env["almonte.police.schedule.generate.wizard"]
        year = wizard_model.new({"date_start": date(2027, 2, 10), "scope": "year"})
        self.assertEqual(year.date_end, date(2027, 12, 31))
        days = wizard_model.new({"date_start": date(2027, 2, 10), "scope": "365"})
        self.assertEqual(days.date_end, date(2028, 2, 9))
        self.assertFalse(wizard_model.new({"date_start": False}).date_end)
        with self.assertRaises(ValidationError):
            self._generate(pattern="6_5", cycle_day=12)
        with self.assertRaises(ValidationError):
            self._generate(pattern="5_2", cycle_day=8)

    def test_generate_defaults_to_selected_employees(self):
        wizard = (
            self.env["almonte.police.schedule.generate.wizard"]
            .with_context(active_model="hr.employee", active_ids=self.employee.ids)
            .create({})
        )
        self.assertEqual(wizard.employee_ids, self.employee)
        self.assertFalse(
            self.env["almonte.police.schedule.generate.wizard"]._default_employee_ids()
        )

    def test_coordinator_can_generate_schedule(self):
        coordinator = self.env["res.users"].create(
            {
                "name": "Coordinador Generador",
                "login": "coordinador_generador",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            self.env.ref(
                                "almonte_police_extra_services.group_police_extra_user"
                            ).id
                        ],
                    )
                ],
            }
        )
        wizard = (
            self.env["almonte.police.schedule.generate.wizard"]
            .with_user(coordinator)
            .create(
                {
                    "employee_ids": [(6, 0, self.employee.ids)],
                    "date_start": date(2027, 6, 1),
                    "scope": "month",
                    "pattern": "5_2",
                }
            )
        )
        wizard.action_generate()
        self.assertEqual(len(self._codes(date(2027, 6, 1), 30)), 30)

    # 5. PNR y decreto de adjudicación

    def test_unpaid_leave_makes_agent_unavailable(self):
        unpaid = self.env.ref("almonte_police_extra_services.shift_unpaid_leave")
        self.assertEqual((unpaid.code, unpaid.state_kind), ("PNR", "absence"))
        service_date = date(2027, 7, 5)
        self._schedule(service_date, unpaid)
        signup = self._signup(self._service(service_date))
        self.assertEqual(signup.priority_tier, "5_unavailable")
        self.assertIn("PNR", signup.availability_status)

    def test_decree_report_renders_assigned_and_waiting_agents(self):
        service_date = date(2027, 7, 8)
        rival = self._employee("Agente en espera", 9503)
        self._schedule(service_date, self.rest)
        self._schedule(service_date, self.afternoon, rival)
        service = self._service(service_date)
        service.department = "Romería"
        self._signup(service, row_number=1)
        self._signup(service, rival, row_number=2)
        service.action_publish()
        service.action_assign()
        self.env.company.external_report_layout_id = self.env.ref(
            "web.external_layout_standard"
        )
        action = service.action_print_decree()
        self.assertEqual(action["report_type"], "qweb-pdf")
        html = (
            self.env["ir.actions.report"]
            ._render_qweb_html(
                "almonte_police_extra_services.report_extra_service_decree",
                service.ids,
            )[0]
            .decode()
        )
        self.assertIn("DECRETO DE ADJUDICACIÓN", html)
        self.assertIn("DIP-9501", html)
        self.assertIn("Agente en espera", html)
        self.assertIn("Romería", html)

    def test_new_menus_are_registered(self):
        def menu(xml_id):
            return self.env.ref(f"almonte_police_extra_services.{xml_id}")

        self.assertEqual(
            menu("menu_schedule_generate").parent_id, menu("menu_services")
        )
        self.assertEqual(
            menu("menu_baremo_reset").parent_id, menu("menu_configuration")
        )
        self.assertEqual(menu("menu_history_backlog").parent_id, menu("menu_reports"))
