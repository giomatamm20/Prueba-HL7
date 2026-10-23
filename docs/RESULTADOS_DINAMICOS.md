# Estudios y resultados sintéticos configurables

El catálogo permite definir estudios y parámetros desde la aplicación sin agregar
ramas al motor. Los equipos HL7 de laboratorio pueden fijarse a una revisión
inmutable. CBC es una definición inicial editable; los generadores no infieren
reglas clínicas, unidades, referencias ni interpretaciones.

## Uso desde la interfaz

1. Abrir **Catálogo de estudios** en el menú lateral. En ventanas estrechas,
   elegirlo en el selector **Sección**. También existe el editor directo en
   `http://localhost:8090/static/catalog.html`.
2. Elegir **Nuevo catálogo**, escribir su nombre y crear un estudio con código,
   descripción y alias opcionales. Aplicar los datos del estudio.
3. Crear cada parámetro: código, descripción, tipo HL7, unidad y referencia
   opcionales. Elegir su generador y pulsar **Aplicar parámetro**. Las flechas
   cambian el orden de parámetros dentro de su grupo.
4. Para repeticiones, crear un grupo con identificador, nombre y cantidad. Puede
   seleccionarse como padre de otro grupo. Los parámetros se agregan a la raíz
   del estudio o al grupo elegido. El ejemplo de bacteriología incluye dos
   aislamientos con antibiogramas hijos.
5. Opcionalmente, seleccionar un parámetro existente con **Editar**, modificar
   su generador y usar **Usar generador del formulario en este escenario**.
   El escenario solo reemplaza ese generador. Puede reemplazar también la
   cantidad de un grupo. NORMAL usa las definiciones base.
6. **Previsualizar catálogo actual** valida la definición y muestra resultados y
   ORU sintéticos. Esta acción no consume las secuencias persistidas ni crea
   una orden.
7. **Guardar nueva revisión**. Guardar como copia crea otro catálogo. Las
   revisiones anteriores siguen disponibles y editarlas crea una revisión nueva.
8. Elegir el equipo, la política para estudios desconocidos y asociar la
   revisión guardada. El equipo debe tener un contrato HL7 de entrada ya
   configurado. **Aplicar salida ORU multiestudio** crea una revisión de salida
   para OBR/OBX/SPM/NTE, conservando el contrato de entrada. Los campos de salida
   pueden ajustarse en **Diseñador HL7 → Salida avanzada**.
9. Recibir la orden o crear una muestra manual, cargarla y procesarla. En Worklist,
   **Ver resultados / HL7** muestra todos los estudios, muestras, parámetros,
   tipos, grupos y la vista previa del contrato de salida activo.
10. Enviar manualmente. Reenviar usa los mismos valores. **Regenerar valores
    explícitamente** permite elegir un escenario, conserva las definiciones de
    la orden y archiva la generación anterior en `resultHistory`.

Un catálogo guardado no se aplica automáticamente a los equipos: se selecciona
la revisión deseada. Una orden recibida mantiene una copia de sus definiciones,
la revisión utilizada y cualquier uso de plantilla genérica, aunque después se
edite el catálogo o se asigne otra revisión al equipo.

## Tipos y generadores

| Tipo | Valor declarado | Generadores |
| --- | --- | --- |
| NM | Número finito | numeric, fixed, choice, sequence |
| ST / TX | Texto | fixed, choice, sequence |
| CE / CWE | Objeto code, name, system | fixed, choice, sequence |

`numeric` usa min, max y decimals (0–8). `choice` elige de la lista declarada.
`sequence` recorre cíclicamente una lista; su posición se persiste por revisión,
estudio, escenario y ruta del parámetro. `fixed` devuelve el valor configurado.
En el editor CE/CWE se ingresa como `código^descripción^sistema`, una opción por
línea. Los indicadores y estados también son datos declarados.

Ejemplo de definición intercambiable desde el editor JSON:

```json
{
  "name": "Catálogo de integración",
  "studies": [{
    "code": "NUEVO",
    "name": "Estudio de prueba",
    "aliases": ["CODIGO_EXTERNO"],
    "parameters": [
      {"code": "P1", "name": "Número", "valueType": "NM", "unit": "u-prueba",
       "referenceRange": "1-10",
       "generator": {"kind": "numeric", "min": 1, "max": 10, "decimals": 2}},
      {"code": "P2", "name": "Texto", "valueType": "TX",
       "generator": {"kind": "fixed", "value": "Texto sintético"}},
      {"code": "P3", "name": "Código", "valueType": "CWE",
       "generator": {"kind": "choice", "values": [
         {"code": "A", "name": "Opción de prueba", "system": "SIM"}]}}
    ],
    "scenarios": {
      "OTRO_VALOR": {"parameters": {"P1": {"kind": "fixed", "value": 20}}}
    }
  }]
}
```

Los escenarios pueden reemplazar generadores por ruta, por ejemplo
`aislamiento.sensibilidad.INTERP`, o cantidades mediante
`groups: {"aislamiento": 2}`. Máximo cuatro niveles de grupos, 50 repeticiones
por grupo y 2000 resultados por orden, considerando sus escenarios.

## Correlación HL7

Se conserva una entrada de `studies` por OBR, incluso cuando el código se repite.
Cada una mantiene su ORC, OBR, identificadores, notas y muestra correspondiente.
En OML^O33 los estudios heredan la muestra del SPM precedente; en OML^O21 un SPM
posterior completa el estudio correspondiente. Los mapeos de `test.code`,
`test.name` y muestra se aplican por grupo.

La salida dinámica configura OBR con `repeatBy: "studies"` y `children` con OBX
`repeatBy: "results"`. Cada OBX usa `result.valueType` en OBX-2,
`result.hl7Value` en OBX-5 y `result.subId` en OBX-4. En el contexto están
disponibles `study`, `test`, `order`, `sample`, `result` y `counter`.

Ejemplo de relaciones de bacteriología:

```text
ORG    OBX-4=aislamiento.1                     microorganismo A
AB     OBX-4=aislamiento.1.sensibilidad.1       antibiótico A del aislamiento 1
CONC   OBX-4=aislamiento.1.sensibilidad.1       concentración de esa prueba
INTERP OBX-4=aislamiento.1.sensibilidad.1       interpretación de esa prueba
ORG    OBX-4=aislamiento.2                     microorganismo B
AB     OBX-4=aislamiento.2.sensibilidad.1       antibiótico A del aislamiento 2
```

Las notas declaradas en parámetros generan NTE inmediatamente después del OBX.
Los valores codificados se escriben como componentes; el texto escapa los
delimitadores para impedir que altere la estructura del mensaje. El escritor
rechaza una salida que perdería grupos por un OBR plano, o que declara un tipo
OBX-2 incompatible con el parámetro.

El editor de contratos conserva el JSON de salida anidado cuando se abre una
revisión dinámica. Mientras **Usar esta definición completa de salida** esté
marcado, ese bloque define la salida al guardar. El canvas de contratos planos
continúa disponible para los demás perfiles.

## Importación

Pegar un ORU solo sugiere estudios y parámetros desde OBR-4, OBX-2, OBX-3,
OBX-6 y OBX-7. No guarda el mensaje, pacientes, identificadores de órdenes ni
valores OBX-5. Los generadores quedan sin configurar y la API rechaza guardar
hasta que el usuario los complete. Los OBX repetidos con el mismo código se
consolidan como sugerencia: las relaciones entre aislamientos y sus grupos se
revisan manualmente.

## Políticas y compatibilidad

- BLOCK rechaza estudios que no son códigos o alias del catálogo y explica la
  causa en el ACK negativo.
- TEMPLATE exige una plantilla elegida del catálogo. Conserva el código recibido
  y registra `fallbackUsed`, `templateCode` y `catalogFallbacks` en la orden.
- Equipos previos sin asociación explícita usan la revisión inicial y la
  plantilla GENERIC en modo compatible; el uso de plantilla queda registrado.
- Los catálogos se asocian a equipos HL7 de laboratorio. PACS y AnaPath mantienen
  sus flujos de imágenes/documentos; ASTM conserva su transporte previo.
- Cambiar el contrato de salida puede modificar la representación de un reenvío;
  no cambia los valores. Si hubo un error de ACK y el contrato no cambió, se
  conserva además el mensaje pendiente para reintentar con sus mismos bytes.
- Los escenarios de resultados pertenecen al catálogo. Las averías/transporte
  del analizador siguen configurándose en la operación del equipo.

## API

| Método y ruta | Función |
| --- | --- |
| GET /api/catalogs | Revisiones, incluidas las históricas |
| POST /api/catalogs | Crear catálogo |
| POST /api/catalogs/{catalogId}/revisions | Agregar revisión inmutable |
| POST /api/catalogs/infer | Sugerir estructura desde rawMessage |
| POST /api/catalogs/preview | Validar y generar vista previa de catalog y scenario |
| PUT /api/analyzers/{id}/catalog | Asociar revisionId, unknownStudyPolicy, genericStudyCode, configureOutbound |
| POST /api/orders/{id}/process | Procesar; resultScenario opcional y sendResult |
| GET /api/orders/{id}/results/preview | Resultados guardados y HL7 del contrato activo |
| POST /api/orders/{id}/results/regenerate | Nueva generación explícita con scenario |

Los catálogos usan la tabla SQLite `study_catalog_revisions`; las secuencias se
guardan en `meta`. Las órdenes conservan los resultados, definiciones, revisión,
escenario y generaciones anteriores dentro de su payload persistido. No se
necesitan reinicios al guardar, asociar revisiones o agregar parámetros.

La evidencia de aceptación está en
[VALIDACION_RESULTADOS_DINAMICOS_2026-10-22.md](VALIDACION_RESULTADOS_DINAMICOS_2026-10-22.md).
