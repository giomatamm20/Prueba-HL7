<?php

namespace App\Libraries;

use DateTimeImmutable;
use DateTimeZone;
use InvalidArgumentException;

class Hl7Parser
{
    public static function segmentsFromMessage(string $rawMessage): array
    {
        $normalized = str_replace(["\r\n", "\n"], "\r", $rawMessage);
        $segments = [];
        foreach (explode("\r", $normalized) as $line) {
            if ($line !== '') {
                $segments[] = explode('|', $line);
            }
        }
        return $segments;
    }

    public static function extractMetadata(string $rawMessage): array
    {
        foreach (self::segmentsFromMessage($rawMessage) as $segment) {
            if (($segment[0] ?? null) === 'MSH') {
                return [
                    'analyzerCode' => self::field($segment, 2),
                    'sendingFacility' => self::field($segment, 3),
                    'messageType' => self::field($segment, 8),
                    'messageControlId' => self::field($segment, 9),
                    'version' => self::field($segment, 11) ?: '2.3',
                ];
            }
        }

        return [
            'analyzerCode' => null,
            'sendingFacility' => null,
            'messageType' => null,
            'messageControlId' => null,
            'version' => '2.3',
        ];
    }

    public static function parseMessage(string $rawMessage): array
    {
        $byType = [];
        foreach (self::segmentsFromMessage($rawMessage) as $segment) {
            $byType[$segment[0]][] = $segment;
        }

        foreach (['MSH', 'PID', 'OBR'] as $required) {
            if (!isset($byType[$required])) {
                throw new InvalidArgumentException("Missing required {$required} segment");
            }
        }

        $metadata = self::extractMetadata($rawMessage);
        if (!$metadata['messageControlId']) {
            throw new InvalidArgumentException('Missing MSH-10 message control ID');
        }
        if (!in_array($metadata['messageType'], ['ORU^R01', 'ORM^O01'], true)) {
            throw new InvalidArgumentException(
                'Unsupported message type: ' . ($metadata['messageType'] ?: 'empty')
            );
        }

        $pid = $byType['PID'][0];
        $obr = $byType['OBR'][0];
        $orc = $byType['ORC'][0] ?? null;
        $patientId = self::field($pid, 3);
        if (!$patientId) {
            throw new InvalidArgumentException('Missing PID-3 patient identifier');
        }

        $isResult = $metadata['messageType'] === 'ORU^R01';
        if ($isResult && !isset($byType['OBX'])) {
            throw new InvalidArgumentException('Missing required OBX segment for ORU result');
        }

        $orderId = $isResult
            ? self::field($obr, 3)
            : (self::field($orc, 2) ?: self::field($obr, 2) ?: self::field($obr, 3));
        if (!$orderId) {
            throw new InvalidArgumentException('Missing order identifier');
        }

        $tests = [];
        foreach ($byType['OBX'] ?? [] as $obx) {
            $code = self::component(self::field($obx, 3), 0);
            $rawValue = self::field($obx, 5);
            if (!$code || $rawValue === null) {
                throw new InvalidArgumentException('OBX segment missing test code or value');
            }
            $tests[] = [
                'code' => $code,
                'value' => self::parseNumericOrText($rawValue, self::field($obx, 2)),
                'unit' => self::field($obx, 6),
                'referenceRange' => self::field($obx, 7),
                'abnormalFlag' => self::field($obx, 8),
                'status' => self::field($obx, 11),
            ];
        }

        $panel = self::field($obr, 4);
        return [
            'eventType' => $isResult ? 'RESULT' : 'ORDER',
            'messageType' => $metadata['messageType'],
            'messageControlId' => $metadata['messageControlId'],
            'analyzerCode' => $metadata['analyzerCode'] ?: 'UNKNOWN',
            'patient' => ['id' => $patientId, 'name' => self::field($pid, 5)],
            'order' => [
                'id' => $orderId,
                'panelCode' => self::component($panel, 0),
                'panelName' => self::component($panel, 1),
                'status' => $orc ? self::field($orc, 1) : ($isResult ? 'RESULTED' : null),
            ],
            'tests' => $tests,
        ];
    }

    public static function buildAck(array $metadata, string $ackCode, ?string $errorMessage = null): string
    {
        $timestamp = (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format('YmdHis');
        $controlId = $metadata['messageControlId'] ?: 'UNKNOWN';
        $trigger = ($metadata['messageType'] ?? null) === 'ORU^R01' ? 'R01' : 'O01';
        $msh = 'MSH|^~\\&|HOST_MIDDLEWARE|CRM|'
            . ($metadata['analyzerCode'] ?: 'ANALYZER') . '|'
            . ($metadata['sendingFacility'] ?: 'LAB') . '|'
            . "{$timestamp}||ACK^{$trigger}|ACK-{$controlId}|P|"
            . ($metadata['version'] ?: '2.3');
        $msa = "MSA|{$ackCode}|{$controlId}" . ($errorMessage ? '|' . substr($errorMessage, 0, 160) : '');

        return $msh . "\r" . $msa . "\r";
    }

    private static function field(?array $segment, int $position): ?string
    {
        if ($segment === null || !array_key_exists($position, $segment) || $segment[$position] === '') {
            return null;
        }
        return $segment[$position];
    }

    private static function component(?string $value, int $position): ?string
    {
        if (!$value) {
            return null;
        }
        $components = explode('^', $value);
        return $components[$position] ?? null;
    }

    private static function parseNumericOrText(string $value, ?string $valueType): float|string
    {
        if ($valueType === 'NM') {
            if (!is_numeric($value)) {
                throw new InvalidArgumentException("Invalid numeric OBX value: {$value}");
            }
            return (float) $value;
        }
        return $value;
    }
}
