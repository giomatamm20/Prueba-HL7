<?php

namespace Config;

use CodeIgniter\Config\BaseConfig;

class Logger extends BaseConfig
{
    public int $threshold = 4;
    public array $handlers = [
        \CodeIgniter\Log\Handlers\FileHandler::class => [
            'handles' => [
                'critical',
                'error',
                'warning',
                'notice',
                'info',
                'debug',
            ],
        ],
    ];
}
