<?php

namespace Config;

use CodeIgniter\Config\BaseConfig;

class Modules extends BaseConfig
{
    public bool $enabled = true;
    public bool $discoverInComposer = true;
    public array $aliases = [];
}
