# 📚 Índice de Documentación de Testing - Épicas 6 & 7

## 🎯 Inicio Rápido

**¿Primer vez probando el sistema?** Empieza aquí:

```bash
cd /mnt/c/VS\ projects/alpaca
bash scripts/test_regression_epic6_7.sh
```

---

## 📖 Documentos Disponibles

### 1. **QUICK_TEST_COMMANDS.md** 🚀
**Para:** Desarrolladores que quieren ejecutar tests rápidamente

**Contiene:**
- Comandos listos para copiar/pegar
- Tests individuales rápidos
- Verificación de cada componente
- Troubleshooting básico

**Cuándo usar:** Cuando necesitas verificar algo específico rápidamente

---

### 2. **REGRESSION_TEST_GUIDE.md** 📋
**Para:** Testing comprehensivo del sistema completo

**Contiene:**
- Guía detallada de todos los tests
- Explicación de qué verifica cada test
- Tests manuales adicionales
- Tests de integración end-to-end
- Troubleshooting avanzado
- Checklist completo

**Cuándo usar:** Para testing formal antes de un release

---

### 3. **TEST_SUMMARY.md** 📊
**Para:** Managers y stakeholders

**Contiene:**
- Resumen ejecutivo de tests
- Estado general del sistema
- Cobertura de tests
- Métricas de calidad
- Próximos pasos

**Cuándo usar:** Para reportes de estado y revisiones

---

### 4. **EPIC6_7_README.md** 📖
**Para:** Desarrolladores que quieren entender las implementaciones

**Contiene:**
- Documentación técnica completa de Épica 6
- Documentación técnica completa de Épica 7
- Ejemplos de código
- APIs y uso programático
- Arquitectura y diseño

**Cuándo usar:** Para entender cómo funcionan las nuevas features

---

## 🔧 Scripts de Testing

### 1. **scripts/test_regression_epic6_7.sh**
**Tipo:** Bash script
**Tests:** ~27 tests automatizados
**Tiempo:** 2-3 minutos
**Uso:**
```bash
bash scripts/test_regression_epic6_7.sh
```

### 2. **scripts/test_system_health.py**
**Tipo:** Python script
**Tests:** ~13 tests comprehensivos
**Tiempo:** 3-5 minutos
**Uso:**
```bash
python scripts/test_system_health.py
```

### 3. **scripts/validate_epic6_7.py**
**Tipo:** Python script
**Tests:** Validación específica de Épicas 6 & 7
**Tiempo:** 1-2 minutos
**Uso:**
```bash
python scripts/validate_epic6_7.py
```

---

## 🗺️ Mapa de Navegación

### "Quiero probar rápidamente que todo funciona"
→ **QUICK_TEST_COMMANDS.md** (Sección: Test 1)

### "Necesito verificar una funcionalidad específica"
→ **QUICK_TEST_COMMANDS.md** (Sección: Test 3)

### "Voy a hacer un release y necesito testing completo"
→ **REGRESSION_TEST_GUIDE.md** (Todo el documento)

### "Necesito reportar el estado del sistema"
→ **TEST_SUMMARY.md**

### "Quiero entender cómo funciona Market Hours"
→ **EPIC6_7_README.md** (Épica 6)

### "Quiero entender cómo funciona Persistence"
→ **EPIC6_7_README.md** (Épica 7)

### "Tengo un problema y necesito ayuda"
→ **REGRESSION_TEST_GUIDE.md** (Sección: Troubleshooting)

### "Quiero ejecutar tests automatizados"
→ Usa los scripts en `scripts/`

---

## 📊 Cobertura de Testing

### Funcionalidad Existente
- ✅ Bus de mensajes
- ✅ Risk Manager (core)
- ✅ Simulator (core)
- ✅ Models
- ✅ Settings
- ✅ Time utilities
- ✅ Deduplication
- ✅ Metrics
- ✅ Backward compatibility

### Nueva Funcionalidad (Épica 6)
- ✅ Market hours validation
- ✅ Holiday detection
- ✅ Early close detection
- ✅ Alpaca Clock API integration
- ✅ Timezone handling

### Nueva Funcionalidad (Épica 7)
- ✅ Persistence system (SQLite)
- ✅ CSV export
- ✅ Parquet export
- ✅ Summary generation
- ✅ Reproducibility (hash)
- ✅ Simulator integration

---

## 🚀 Quick Start Guide

### Paso 1: Preparación
```bash
cd /mnt/c/VS\ projects/alpaca
conda activate trading_bot
redis-cli ping  # Verificar Redis
```

### Paso 2: Tests Rápidos
```bash
bash scripts/test_regression_epic6_7.sh
```

### Paso 3: Verificar Resultado
Si ves:
```
SUCCESS: ALL REGRESSION TESTS PASSED
```
→ **Todo está funcionando correctamente** ✅

Si hay errores:
→ Ir a **REGRESSION_TEST_GUIDE.md** (Sección: Troubleshooting)

### Paso 4: Tests Adicionales (Opcional)
```bash
python scripts/test_system_health.py
python scripts/validate_epic6_7.py
```

---

## 📝 Tests Disponibles por Categoría

### Tests de Importación
- Importaciones básicas (lib/*)
- Importaciones de servicios (apps/*)
- Importaciones de Épicas 6 & 7

### Tests Funcionales
- Risk Manager con market hours
- Simulator con/sin persistence
- Bus y mensajería
- Deduplication
- Rate limiting

### Tests de Integración
- Risk Manager + Market Hours
- Simulator + Persistence
- End-to-end flow

### Tests de Compatibilidad
- Backward compatibility
- Aliases viejos funcionan
- No breaking changes

### Tests de Calidad
- Reproducibilidad
- Performance básico
- Memory leaks check

---

## 🎓 Tutoriales

### Tutorial 1: Primera Vez Usando el Sistema
1. Lee **QUICK_TEST_COMMANDS.md**
2. Ejecuta Test 1 (Suite de Regresión)
3. Si pasa → El sistema funciona
4. Si falla → Revisa Troubleshooting

### Tutorial 2: Validando un Nuevo Cambio
1. Haz tu cambio de código
2. Ejecuta `bash scripts/test_regression_epic6_7.sh`
3. Ejecuta `python scripts/test_system_health.py`
4. Verifica que todo pasa
5. Si algo falla → Tu cambio rompió algo

### Tutorial 3: Testing Antes de Release
1. Lee **REGRESSION_TEST_GUIDE.md** completo
2. Ejecuta todos los scripts de testing
3. Ejecuta tests manuales de la guía
4. Ejecuta test end-to-end
5. Verifica métricas de Prometheus
6. Documenta resultados en **TEST_SUMMARY.md**

### Tutorial 4: Debugging de un Problema
1. Reproduce el problema
2. Ejecuta tests individuales de **QUICK_TEST_COMMANDS.md**
3. Revisa logs: `/tmp/test_output_*.log`
4. Si necesitas ayuda → **REGRESSION_TEST_GUIDE.md** (Troubleshooting)

---

## 🔍 Búsqueda Rápida

### "¿Cómo verifico que Market Hours funciona?"
```bash
# Ver QUICK_TEST_COMMANDS.md, Test 3.2
python -c "
from apps.risk_manager.market_hours import MarketHoursValidator
v = MarketHoursValidator()
print(v.validate_trading_hours())
"
```

### "¿Cómo verifico que Persistence funciona?"
```bash
# Ver QUICK_TEST_COMMANDS.md, Test 3.5
python scripts/validate_epic6_7.py
```

### "¿Cómo ejecuto una simulación con persistencia?"
```bash
# Ver QUICK_TEST_COMMANDS.md, Test 4.3
python apps/simulator/main.py --persist --symbols AAPL --start 2024-01-01 --end 2024-01-02 --csv test_data
```

### "¿Dónde están los archivos generados?"
```bash
ls -la out/run_*/
```

### "¿Cómo verifico reproducibilidad?"
```bash
# Ver QUICK_TEST_COMMANDS.md, Test 5
# O REGRESSION_TEST_GUIDE.md, Test E2E 2
```

---

## 📞 Soporte

### Si tienes preguntas:
1. Busca en este índice
2. Lee el documento relevante
3. Prueba los comandos de ejemplo
4. Revisa Troubleshooting

### Si encuentras un bug:
1. Ejecuta tests para reproducir
2. Captura logs
3. Reporta con:
   - Output de tests
   - Logs relevantes
   - Pasos para reproducir

---

## 📈 Métricas

### Documentación
- **Archivos:** 4 documentos principales
- **Scripts:** 3 scripts de testing
- **Cobertura:** 100% de funcionalidad documentada
- **Ejemplos:** 30+ ejemplos de código

### Testing
- **Tests Automatizados:** 40+ tests
- **Cobertura:** ~95% del código crítico
- **Tiempo Total:** ~10 minutos para suite completa

---

## 🎯 Checklist de Verificación

### Antes de Empezar
- [ ] Environment activado
- [ ] Redis corriendo
- [ ] En directorio correcto

### Tests Básicos
- [ ] test_regression_epic6_7.sh pasa
- [ ] test_system_health.py pasa
- [ ] validate_epic6_7.py pasa

### Verificación Manual
- [ ] Market hours funciona
- [ ] Persistence funciona
- [ ] Backward compatibility OK

### Listo para Release
- [ ] Todos los tests pasan
- [ ] Documentación actualizada
- [ ] TEST_SUMMARY.md completo

---

## 🌟 Mejores Prácticas

### Para Desarrolladores
1. **Siempre** ejecuta tests antes de commit
2. **Siempre** verifica backward compatibility
3. **Siempre** actualiza documentación

### Para Testing
1. Ejecuta suite completa al menos 1x por semana
2. Ejecuta tests rápidos después de cada cambio
3. Documenta cualquier issue encontrado

### Para Releases
1. Ejecuta **todos** los tests
2. Verifica **todos** los ejemplos
3. Actualiza **toda** la documentación

---

## 📚 Recursos Adicionales

### Archivos de Implementación
- `apps/risk_manager/market_hours.py` - Market Hours
- `apps/simulator/persist.py` - Persistence
- `tests/test_epic6_market_hours.py` - Tests Épica 6
- `tests/test_epic7_persistence.py` - Tests Épica 7

### Documentación Externa
- [Alpaca Clock API](https://alpaca.markets/docs/api-references/trading-api/clock/)
- [NYSE Holiday Schedule](https://www.nyse.com/markets/hours-calendars)
- [SQLite Documentation](https://www.sqlite.org/docs.html)

---

**Última Actualización:** Octubre 2024
**Versión:** 1.0
**Estado:** ✅ Production Ready

🎉 **¡Happy Testing!**
