<?php

namespace Config;

use CodeIgniter\Config\BaseConfig;

class Filters extends BaseConfig
{
    public array $aliases = [];
    public array $globals = [
        'before' => [],
        'after' => [],
    ];
    public array $methods = [];
    public array $filters = [];
}
