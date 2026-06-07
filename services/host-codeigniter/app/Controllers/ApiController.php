<?php

namespace App\Controllers;

use App\Models\CrmStore;
use CodeIgniter\HTTP\ResponseInterface;
use CodeIgniter\RESTful\ResourceController;
use DateTimeImmutable;
use DateTimeZone;
use RuntimeException;

class ApiController extends ResourceController
{
    private const START_BLOCK = "\x0b";
    private const END_BLOCK = "\x1c\r";

    private CrmStore $store;

    public function __construct()
    {
        $this->store = new CrmStore();
        $this->store->initialize();
    }

    public function status(): ResponseInterface
    {
        return $this->respond([
            'listener' => $this->store->listenerSnapshot(),
            'database' => $this->store->statusSummary(),
        ]);
    }

    public function messages(): ResponseInterface
    {
        $limit = $this->boundedLimit((int) ($this->request->getGet('limit') ?? 25), 1, 200);
        return $this->respond($this->store->messages($limit));
    }

    public function patients(): ResponseInterface
    {
        return $this->respond($this->store->patients());
    }

    public function orders(): ResponseInterface
    {
        return $this->respond($this->store->orders());
    }

    public function results(): ResponseInterface
    {
        $limit = $this->boundedLimit((int) ($this->request->getGet('limit') ?? 100), 1, 500);
        return $this->respond($this->store->results($limit));
    }

    public function testCatalog(): ResponseInterface
    {
        return $this->respond($this->store->testCatalog());
    }

    public function createTestCatalogItem(): ResponseInterface
    {
        $payload = $this->jsonPayload();
        $code = strtoupper(trim((string) ($payload['code'] ?? '')));
        $name = trim((string) ($payload['name'] ?? ''));
        if ($code === '' || $name === '') {
            return $this->failValidationErrors('code y name son requeridos');
        }

        return $this->respondCreated($this->store->createTestCatalogItem([
            'code' => $code,
            'name' => $name,
            'analyzer_code' => trim((string) ($payload['analyzerCode'] ?? 'ABBOTT_RUBY')) ?: 'ABBOTT_RUBY',
            'sort_order' => (int) ($payload['sortOrder'] ?? 100),
        ]));
    }

    public function outboundOrders(): ResponseInterface
    {
        $limit = $this->boundedLimit((int) ($this->request->getGet('limit') ?? 25), 1, 100);
        return $this->respond($this->store->outboundOrders($limit));
    }

    public function createOutboundOrder(): ResponseInterface
    {
        $payload = $this->jsonPayload();
        $tests = array_values(array_unique(array_filter(array_map(
            static fn ($value) => strtoupper(trim((string) $value)),
            $payload['tests'] ?? []
        ))));

        $order = [
            'orderExternalId' => trim((string) ($payload['orderId'] ?? '')),
            'sampleId' => trim((string) ($payload['sampleId'] ?? '')),
            'patientExternalId' => trim((string) ($payload['patientId'] ?? '')),
            'patientName' => trim((string) ($payload['patientName'] ?? '')),
            'priority' => trim((string) ($payload['priority'] ?? 'ROUTINE')) ?: 'ROUTINE',
            'analyzerCode' => trim((string) ($payload['analyzerCode'] ?? 'ABBOTT_RUBY')) ?: 'ABBOTT_RUBY',
            'destinationHost' => trim((string) ($payload['destinationHost'] ?? env('RUBY_ORDER_HOST', 'localhost'))) ?: 'localhost',
            'destinationPort' => (int) ($payload['destinationPort'] ?? env('RUBY_ORDER_PORT', 5001)),
            'requestedTests' => $tests,
        ];

        if ($order['orderExternalId'] === '' || $order['sampleId'] === '' || $order['patientExternalId'] === '') {
            return $this->failValidationErrors('orderId, sampleId y patientId son requeridos');
        }
        if ($tests === []) {
            return $this->failValidationErrors('Selecciona al menos un examen');
        }
        if ($order['destinationPort'] < 1 || $order['destinationPort'] > 65535) {
            return $this->failValidationErrors('destinationPort debe ser un puerto valido');
        }

        $rawMessage = $this->buildOrmOrder($order);
        $ack = null;
        $status = 'ACKED';
        $errorMessage = null;
        try {
            $ack = $this->sendMllp($order['destinationHost'], $order['destinationPort'], $rawMessage);
        } catch (RuntimeException $error) {
            $status = 'ERROR';
            $errorMessage = $error->getMessage();
        }

        $ackCode = $this->ackCode($ack);
        if ($ackCode !== null && $ackCode !== 'AA') {
            $status = 'REJECTED';
        }

        $saved = $this->store->saveOutboundOrder([
            ...$order,
            'rawMessage' => $rawMessage,
            'ackCode' => $ackCode,
            'ackMessage' => $ack,
            'status' => $status,
            'errorMessage' => $errorMessage,
        ]);

        return $status === 'ERROR' ? $this->respond($saved, 502) : $this->respondCreated($saved);
    }

    private function boundedLimit(int $value, int $minimum, int $maximum): int
    {
        return max($minimum, min($maximum, $value));
    }

    private function jsonPayload(): array
    {
        $payload = $this->request->getJSON(true);
        return is_array($payload) ? $payload : [];
    }

    private function buildOrmOrder(array $order): string
    {
        $timestamp = (new DateTimeImmutable('now', new DateTimeZone('UTC')))->format('YmdHis');
        $controlId = 'ORM-' . $order['orderExternalId'] . '-' . $timestamp;
        $patientName = $order['patientName'] !== '' ? $order['patientName'] : 'PACIENTE^SIN_NOMBRE';
        $segments = [
            "MSH|^~\\&|LIS|CLINIC|ANALYZER|SIMLAB|{$timestamp}||ORM^O01|{$controlId}|P|2.5.1",
            "PID|1||{$order['patientExternalId']}||{$patientName}",
            "ORC|NW|{$order['orderExternalId']}",
        ];

        foreach ($order['requestedTests'] as $index => $testCode) {
            $position = $index + 1;
            $segments[] = "OBR|{$position}|{$order['orderExternalId']}|{$order['sampleId']}|{$testCode}^{$testCode}|||||||||||||||||||||||{$order['priority']}";
        }

        return implode("\r", $segments) . "\r";
    }

    private function sendMllp(string $host, int $port, string $message): string
    {
        $socket = @stream_socket_client("tcp://{$host}:{$port}", $errno, $errstr, 5);
        if ($socket === false) {
            throw new RuntimeException("No se pudo conectar a {$host}:{$port}: {$errstr} ({$errno})");
        }
        stream_set_timeout($socket, 5);
        fwrite($socket, self::START_BLOCK . $message . self::END_BLOCK);
        $buffer = '';
        while (!feof($socket)) {
            $chunk = fread($socket, 8192);
            if ($chunk === false || $chunk === '') {
                $meta = stream_get_meta_data($socket);
                if ($meta['timed_out'] ?? false) {
                    fclose($socket);
                    throw new RuntimeException('Timeout esperando ACK del analizador');
                }
                break;
            }
            $buffer .= $chunk;
            $start = strpos($buffer, self::START_BLOCK);
            $end = $start === false ? false : strpos($buffer, self::END_BLOCK, $start + 1);
            if ($start !== false && $end !== false) {
                fclose($socket);
                return substr($buffer, $start + 1, $end - $start - 1);
            }
        }
        fclose($socket);
        throw new RuntimeException('El analizador cerro la conexion sin ACK MLLP');
    }

    private function ackCode(?string $ack): ?string
    {
        if ($ack === null) {
            return null;
        }
        foreach (explode("\r", str_replace(["\r\n", "\n"], "\r", $ack)) as $segment) {
            $parts = explode('|', $segment);
            if (($parts[0] ?? null) === 'MSA') {
                return $parts[1] ?? null;
            }
        }
        return null;
    }
}
