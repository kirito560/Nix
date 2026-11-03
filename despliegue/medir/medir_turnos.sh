#!/bin/bash
# Mide la latencia de turno a turno con un proveedor de LLM: reinicia Nix con ese proveedor, manda un WAV de
# frases como si fuera el PC y resume el log de latencia del servidor (p50/p95 por etapa).
#
#   despliegue/medir/medir_turnos.sh <groq|freellmapi> [turnos=20]
#
# El proveedor se pasa al servicio con `systemctl --user set-environment` (gana al .env, que no se toca) y al
# terminar se quita y Nix se reinicia con lo que diga el .env. Los archivos van a ~/.cache/nix-medir.
set -euo pipefail
PROVEEDOR=${1:?uso: medir_turnos.sh <groq|freellmapi> [turnos]}
TURNOS=${2:-20}
SERVIDOR=${NIX_SERVIDOR_WS:-ws://192.168.1.50:8765}
D=${XDG_CACHE_HOME:-$HOME/.cache}/nix-medir
cd "$(dirname "$0")/../.."
mkdir -p "$D"

WAV=$D/turnos_$TURNOS.wav
[ -f "$WAV" ] || { echo "== creando $WAV ($TURNOS turnos)"; .venv/bin/python despliegue/medir/hacer_wav_turnos.py "$WAV" "$TURNOS"; }

restaurar() { systemctl --user unset-environment LLM_PROVEEDOR; systemctl --user restart nix.service; }
trap restaurar EXIT
systemctl --user set-environment LLM_PROVEEDOR="$PROVEEDOR"
desde=$(date '+%Y-%m-%d %H:%M:%S')
systemctl --user restart nix.service
# Espera a que la caché de voz esté llena (dos líneas «caché de voz»: frases fijas y aperturas con cada emoción).
for _ in $(seq 120); do
    [ "$(journalctl --user -u nix.service --since "$desde" --no-pager -o cat | grep -c 'caché de voz')" -ge 2 ] && break
    sleep 1
done
sleep 2

ETIQUETA=${PROVEEDOR}_$(date +%Y%m%d_%H%M%S)
echo "== midiendo $TURNOS turnos con LLM_PROVEEDOR=$PROVEEDOR (unos $((TURNOS * 20 / 60 + 1)) minutos)"
.venv/bin/python -m client.main --servidor "$SERVIDOR" --wav "$WAV" --salida "$D/respuesta_$ETIQUETA.wav" \
    --espera 15 > "$D/cliente_$ETIQUETA.log" 2>&1
sleep 2
LOG=$D/servidor_$ETIQUETA.log
journalctl --user -u nix.service --since "$desde" --no-pager -o cat > "$LOG"

echo "== proveedor: $(grep -m1 '🧠 LLM:' "$LOG" | sed -E 's/.*🧠 LLM: ([^|]*).*/\1/')"
echo "== turnos entendidos: $(grep -c '🗣️ usuario' "$LOG")   descartados: $(grep -c '🙉' "$LOG" || true)"
echo "== RESPALDO: $(grep -c RESPALDO "$LOG" || true)   sin respuesta del LLM: $(grep -c '❌ el LLM' "$LOG" || true)"
echo "== adelanto: cierres tempranos $(grep -c '⏩ cierre temprano' "$LOG" || true); llega (p50) a $(grep -o 'listo a [0-9]* ms' "$LOG" | grep -o '[0-9]*' | sort -n | awk '{a[NR]=$1} END {if (NR) print a[int((NR+1)/2)]; else print "-"}') ms de tu voz; el VAD cierra a $(grep -o 'turno cerrado a [0-9]* ms' "$LOG" | grep -o '[0-9]*' | sort -n | awk '{a[NR]=$1} END {if (NR) print a[int((NR+1)/2)]; else print "-"}') ms"
echo "== memoria: $(grep -o '[0-9]* recuerdo(s) guardado(s)' "$LOG" | grep -o '^[0-9]*' | paste -sd+ | bc 2>/dev/null || echo 0) recuerdos nuevos; búsquedas lentas (>15 ms): $(grep -c 'buscar recuerdos tardó' "$LOG" || true)"
echo "== emociones:"; grep -o '🎭 emoción: .*' "$LOG" | sort | uniq -c || true
if [ "$PROVEEDOR" = freellmapi ]; then
    echo "== modelos que eligió el router:"; grep -o 'el router eligió .*' "$LOG" | sort | uniq -c || true
fi
echo "== latencia acumulada (ms)"
grep "⏱️ acumulado" "$LOG" | tail -1 | sed -E 's/.*acumulado \| //' | tr '|' '\n'
echo "== log completo: $LOG"
