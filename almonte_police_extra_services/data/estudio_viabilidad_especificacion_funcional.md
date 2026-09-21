# XTENDOO SOFTWARE, S.L.U.

## Estudio de viabilidad y especificación funcional

**Módulo Odoo 19 — Gestión de Cuadrantes y Servicios Extraordinarios de la Policía
Local** **Cliente:** Ayuntamiento de Almonte — Departamento de Movilidad y Transporte
**Interlocutor:** José María Sosa **Elaborado por:** Manuel Calero Solís — Xtendoo
**Versión:** 1 · 11 de septiembre de 2026 · Documento vivo de trabajo

## Índice

1. Contexto y antecedentes
2. Objetivo del documento
3. Análisis del Excel recibido
4. Alcance y decisiones de diseño
5. Modelo de datos propuesto
6. Lógica de negocio
7. Ventajas frente al Excel actual
8. Hoja de ruta propuesta
9. Preguntas abiertas
10. Próximos pasos

## 1. Contexto y antecedentes

El 31 de agosto de 2026, José María Sosa, del Departamento de Movilidad y Transporte del
Ayuntamiento de Almonte, remitió a Xtendoo un correo con asunto «Programa de Excel»,
adjuntando el archivo `SERVICIOS EXTRAORDINARIOS PRUEBA.xlsx`. El Excel recoge la lógica
que el departamento utiliza para gestionar los servicios extraordinarios de la Policía
Local y solicita expresamente añadir una hoja con el precio de los servicios y el total
del montante económico por policía.

El 1 de septiembre de 2026, Manuel Calero (Xtendoo) respondió proponiendo trasladar la
funcionalidad a Odoo 19 Community para centralizar la ficha de cada policía, el
cuadrante de turnos, las ausencias, los servicios extraordinarios, la disponibilidad, el
reparto equitativo, la adjudicación, el histórico y los informes, incluido el cálculo
económico solicitado.

Este documento analiza la lógica del Excel y la traduce a una especificación funcional y
de modelo de datos para el módulo `almonte_police_extra_services`, ubicado en el
repositorio `custom-almonte-police`.

## 2. Objetivo del documento

Servir como documento de trabajo vivo para analizar la lógica validada en el Excel,
fijar el alcance y las decisiones de diseño, proponer el modelo de datos y la lógica de
negocio en Odoo 19, identificar las preguntas abiertas para la reunión con José María y
establecer una hoja de ruta por fases.

## 3. Análisis del Excel recibido

El archivo contiene seis hojas:

| Hoja                   | Contenido                                                              | Función                               |
| ---------------------- | ---------------------------------------------------------------------- | ------------------------------------- |
| `Policías`             | Ficha maestra de 59 agentes: ID, nombre, categoría, DIP y grupo.       | Base central de personal.             |
| `Cuadrante`            | Una fila por agente y una columna por día, con el turno diario.        | Planificación de turnos y ausencias.  |
| `Historico_Extras`     | Fecha, empleado, turno cubierto, días extra y acumulado.               | Fuente de acumulados para el reparto. |
| `Panel_Control`        | Estado del día, turno anterior, descanso de 24 horas y disponibilidad. | Motor de reglas de disponibilidad.    |
| `Asignacion_Inscritos` | Ofertas, voluntarios, score, ranking y adjudicación.                   | Motor de adjudicación equitativa.     |
| `Resumen_Total_Extras` | Total de días extra por agente.                                        | Informe agregado.                     |

### 3.1. Códigos de turno utilizados

La leyenda define:

- `M`: mañana.
- `T`: tarde.
- `N`: noche.
- `D`: descanso/libre.
- `L`: libre.
- `VAC`: vacaciones.
- `BAJA`: baja.
- `AP`: asuntos propios.
- `HS`: horas sindicales.

El código `L` ha sido confirmado por el cliente como **Libre** y se trata como un estado
de descanso a efectos de disponibilidad y prioridad.

### 3.2. Lógica de disponibilidad

Para una fecha de servicio, el panel calcula el estado del cuadrante de ese día, el
turno del día anterior y la validación del descanso de 24 horas. Si el agente trabajó de
noche el día anterior, queda restringido al comprobar disponibilidad para un turno de
mañana.

La clasificación resultante es:

1. **No disponible**, si está en vacaciones, baja, asuntos propios u horas sindicales.
2. **Restringido**, si incumple el descanso configurado.
3. **Disponible doblando**, si tiene un turno asignado ese día.
4. **Prioritario libre**, si está libre y no acumula extras.
5. **Elegible libre**, si está libre y sí acumula extras.

### 3.3. Lógica de adjudicación equitativa

Para cada inscrito se calcula un score:

- No apto: `99999`.
- Libre: `extras_acumulados × 1000 + número_de_fila`.
- Doblado: `50000 + extras_acumulados × 1000 + número_de_fila`.

Las plazas se adjudican por score ascendente. Los agentes libres siempre tienen
prioridad sobre quienes doblan turno. Los no aptos quedan excluidos del reparto.

### 3.4. Limitación detectada

El histórico se alimenta manualmente y de forma independiente de la adjudicación, con
riesgo de doble anotación u olvidos. En Odoo, la adjudicación generará automáticamente
el histórico.

El Excel no incluye precios ni cálculo económico; esta es la ampliación solicitada por
José María y forma parte del alcance inicial.

## 4. Alcance y decisiones de diseño

- **Módulo exclusivo para Almonte:** repositorio `custom-almonte-police` y módulo
  `almonte_police_extra_services`.
- **Enfoque híbrido:** reglas y vocabulario adaptados a Almonte, con catálogos
  configurables para turnos, restricciones, servicios y tarifas.
- **Cuadrante y ausencias propios:** una asignación por agente y día, sin usar
  `hr_holidays` ni `hr_attendance`.
- **Agentes sobre `hr.employee`:** se añaden categoría policial, DIP, grupo e ID
  empleado policial.
- **Cálculo económico desde el primer diseño:** tarifa fija o tarifa por horas, según la
  configuración del tipo de servicio.

## 5. Modelo de datos propuesto

| Modelo                                 | Contenido                                             | Sustituye                |
| -------------------------------------- | ----------------------------------------------------- | ------------------------ |
| `hr.employee` ampliado                 | Categoría, DIP, grupo e ID policial.                  | `Policías`.              |
| `almonte.police.shift.type`            | Código, nombre, tipo de estado, noche y orden.        | Códigos del `Cuadrante`. |
| `almonte.police.shift.restriction`     | Incompatibilidades entre turno anterior y requerido.  | Regla de descanso.       |
| `almonte.police.schedule.assignment`   | Una asignación por agente y fecha.                    | Rejilla del `Cuadrante`. |
| `almonte.police.service.type`          | Tipo de servicio, tarifa fija o por hora y moneda.    | Hoja de precios nueva.   |
| `almonte.police.extra.service`         | Oferta, fecha, turno, plazas, tipo y motivo.          | Ofertas de extras.       |
| `almonte.police.extra.service.signup`  | Inscripción, disponibilidad, score, ranking y estado. | Registro de inscritos.   |
| `almonte.police.extra.service.history` | Servicio realizado, horas, importe y agente.          | `Historico_Extras`.      |
| `almonte.police.extra.summary`         | Servicios, horas e importe acumulado por policía.     | `Resumen_Total_Extras`.  |

## 6. Lógica de negocio

- El estado del día y del día anterior se obtiene de las asignaciones diarias.
- Las restricciones se consultan desde el catálogo configurable.
- El comportamiento de cada tipo se define mediante sus campos de estado (turno,
  descanso o ausencia), indicador de turno nocturno y registros de restricción; no
  depende de códigos de turno escritos en la lógica.
- Un turno de mañana permite realizar un extra de tarde y uno de tarde permite realizar
  un extra de noche. Un turno de noche restringe únicamente el extra de mañana del día
  siguiente; el extra de tarde sí es posible.
- No pueden realizar extras los agentes con vacaciones, baja, asuntos propios, permiso,
  días de compensación, horas sindicales o de compensación, días de antigüedad, ingreso
  familiar o libre disposición.
- La categoría policial (incluida la condición de oficial) no modifica la prioridad de
  adjudicación salvo que una oferta indique expresamente una categoría requerida.
- Los extras acumulados se calculan sobre el histórico automático.
- El score y el ranking se recalculan al inscribir o actualizar la oferta.
- El botón **Adjudicar plazas** selecciona por ranking y genera automáticamente el
  histórico de las plazas adjudicadas.
- El importe se calcula mediante tarifa fija o `horas × tarifa_hora`.
- El resumen permite consultar servicios, horas e importes por policía y periodo.
- La prioridad de reparto suma únicamente los servicios del trimestre de la oferta. El
  histórico anterior se conserva y puede consultarse, pero no contamina el cálculo del
  trimestre siguiente.

## 7. Ventajas frente al Excel actual

- Información centralizada y disponibilidad actualizada automáticamente.
- Trazabilidad del proceso y eliminación de la doble anotación manual.
- Cálculo económico por servicio y acumulado por policía.
- Catálogos configurables sin modificar código.
- Control de acceso por roles.

## 8. Hoja de ruta propuesta

### Fase 1 — Fichas y cuadrante base

Ampliación de `hr.employee`, tipos de turno, asignación diaria y vista de cuadrante.

### Fase 2 — Disponibilidad y adjudicación

Restricciones, ofertas, inscripción, score, ranking, adjudicación e histórico.

### Fase 3 — Módulo económico

Tipos de servicio, tarifas, importes e informe económico por policía, grupo y periodo.

### Fase 4 — Evolutivos

Portal de autoinscripción, notificaciones e integración futura con nómina, sujetos a
validación de permisos y necesidades del cliente.

## 9. Preguntas abiertas para validar con José María

El significado de `L` ya está resuelto: **Libre**. Permanecen abiertas estas decisiones
antes de cerrar definitivamente las reglas:

1. Además de noche→mañana, ¿existen otras reglas obligatorias de descanso, horas mínimas
   entre turnos o máximos de extras por semana/mes?
2. ¿El criterio de equidad usa todo el histórico o se reinicia por mes, año o ejercicio?
3. ¿El importe depende solo del tipo de servicio o también de las horas realmente
   realizadas?
4. ¿Quién puede publicar ofertas y quién puede adjudicarlas?
5. ¿Se apuntarán los agentes mediante portal/app o registrará las inscripciones el
   coordinador?
6. ¿Debe migrarse a Odoo el histórico anterior a la puesta en marcha?

## 10. Próximos pasos

1. Revisar internamente este documento con las decisiones pendientes.
2. Compartirlo con José María Sosa para confirmar las reglas.
3. Cerrar permisos, periodicidad del histórico, descansos y modelo económico.
4. Continuar el desarrollo del módulo `almonte_police_extra_services` por fases.

## 11. Fase 4 implementada: autoservicio de agentes (autoinscripción)

Como primer evolutivo de la Fase 4 se ha implementado el autoservicio descrito en la
pregunta abierta nº 5: los propios agentes pueden autenticarse y apuntarse como
voluntarios, sin depender del coordinador para inscribirse.

### 11.1. Grupos de acceso

Se ha añadido una categoría de módulo **Policía Local** con tres niveles, mostrados como
una única selección en _Ajustes > Usuarios_ (igual que hacen Ventas o RRHH):

- **Agente (autoservicio)**: solo ve los servicios publicados (o aquellos en los que ya
  está inscrito), su propia ficha, su propio cuadrante, su propio histórico/resumen
  económico y puede apuntarse o retirarse como voluntario.
- **Coordinador**: el rol ya existente (`group_police_extra_user`), sin cambios
  funcionales, ahora también implica el grupo Agente.
- **Responsable**: acceso completo (ya existente, `group_police_extra_manager`), además
  puede crear el usuario Odoo de un agente.

Las reglas de registro (`ir.rule`) siguen el patrón estándar de Odoo: una regla
restrictiva para el grupo Agente y una regla permisiva para Coordinador/Responsable;
como estos últimos implican al primero, Odoo combina ambas con `OR` y el resultado es
acceso completo para coordinadores/responsables y acceso restringido solo para agentes
puros.

### 11.2. Autoinscripción y notificación

- Botón **Apuntarme como voluntario** en la oferta (solo visible si está publicada, el
  usuario tiene un agente vinculado y no está ya inscrito).
- Botón **Retirar** en “Mis inscripciones” mientras la inscripción no se haya resuelto.
- Al adjudicar plazas (`action_assign`), cada agente inscrito recibe un email
  (plantillas estándar `mail.template`, sin Enterprise) indicando si ha sido adjudicado
  o ha quedado en lista de espera.

### 11.3. Alta de usuarios

Un responsable puede crear el usuario Odoo de un agente desde su ficha (botón **Crear
usuario Odoo**). El proceso reutiliza el mecanismo estándar de invitación de Odoo
(`auth_signup`): nunca se maneja ni se muestra ninguna contraseña, el propio agente la
establece a través del enlace de invitación que recibe por correo.

**Decisión de alcance**: no se ha migrado ni creado en bloque un usuario Odoo para los
59 agentes de `data/police_employees.xml` porque esa relación maestra no incluye ningún
email de trabajo (no existe un patrón seguro de login). Se ha optado por dar la
herramienta al responsable para darlos de alta uno a uno según se vaya confirmando el
email de cada agente con el Ayuntamiento.
