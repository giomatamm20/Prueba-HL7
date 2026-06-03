<?php

namespace App\Commands;

use App\Libraries\Hl7Parser;
use App\Models\CrmStore;
use CodeIgniter\CLI\BaseCommand;
use CodeIgniter\CLI\CLI;
use Throwable;

class MllpListen extends BaseCommand
{
    protected $group = 'HL7';
    protected $name = 'hl7:mllp-listen';
    protected $description = 'Starts the HL7 MLLP TCP listener and returns ACK/NAK responses.';

    private const START_BLOCK = "\x0b";
    private const END_BLOCK = "\x1c\r";

    public function run(array $params)
    {
        $host = env('MLLP_HOST', '0.0.0.0');
        $port = (int) env('MLLP_PORT', 2575);
        $store = new CrmStore();
        $store->initialize();
        $server = @stream_socket_server(
            "tcp://{$host}:{$port}",
            $errno,
            $errstr,
            STREAM_SERVER_BIND | STREAM_SERVER_LISTEN
        );

        if ($server === false) {
            CLI::error("Cannot start MLLP listener on {$host}:{$port}: {$errstr} ({$errno})");
            return EXIT_ERROR;
        }

        CLI::write("MLLP listener active on {$host}:{$port}", 'green');
        while ($connection = @stream_socket_accept($server, -1, $peer)) {
            $sourceIp = explode(':', (string) $peer)[0] ?: 'unknown';
            $this->handleConnection($connection, $sourceIp, $store);
            fclose($connection);
        }

        fclose($server);
        return EXIT_SUCCESS;
    }

    /**
     * Handles fragmented MLLP frames on a single TCP connection.
     *
     * @param resource $connection
     */
    private function handleConnection($connection, string $sourceIp, CrmStore $store): void
    {
        $buffer = '';
        while (!feof($connection)) {
            $chunk = fread($connection, 8192);
            if ($chunk === false || $chunk === '') {
                break;
            }

            $buffer .= $chunk;
            while (true) {
                $start = strpos($buffer, self::START_BLOCK);
                $end = $start === false ? false : strpos($buffer, self::END_BLOCK, $start + 1);
                if ($start === false) {
                    $buffer = substr($buffer, -1);
                    break;
                }
                if ($end === false) {
                    $buffer = substr($buffer, $start);
                    break;
                }

                $rawMessage = substr($buffer, $start + 1, $end - $start - 1);
                $buffer = substr($buffer, $end + strlen(self::END_BLOCK));
                $ack = $this->processMessage($rawMessage, $sourceIp, $store);
                fwrite($connection, self::START_BLOCK . $ack . self::END_BLOCK);

                if ($buffer === '') {
                    break;
                }
            }
        }
    }

    private function processMessage(string $rawMessage, string $sourceIp, CrmStore $store): string
    {
        $metadata = Hl7Parser::extractMetadata($rawMessage);
        try {
            $parsed = Hl7Parser::parseMessage($rawMessage);
            $ack = Hl7Parser::buildAck($metadata, 'AA');
            $store->saveReceived($rawMessage, $sourceIp, $parsed, $ack);
            $store->saveListenerState($sourceIp, 'AA', $metadata['messageControlId']);
            CLI::write('Stored ' . $metadata['messageControlId'], 'green');
            return $ack;
        } catch (\InvalidArgumentException $error) {
            $ack = Hl7Parser::buildAck($metadata, 'AE', $error->getMessage());
            $store->saveRejected(
                $rawMessage,
                $sourceIp,
                $metadata['analyzerCode'],
                $metadata['messageType'],
                $metadata['messageControlId'],
                $ack,
                $error->getMessage()
            );
            $store->saveListenerState($sourceIp, 'AE', $metadata['messageControlId']);
            CLI::error('Rejected message: ' . $error->getMessage());
            return $ack;
        } catch (Throwable $error) {
            $store->saveListenerState($sourceIp, 'AE', $metadata['messageControlId']);
            CLI::error('Processing failure: ' . $error->getMessage());
            return Hl7Parser::buildAck($metadata, 'AE', 'Host processing failure');
        }
    }
}
