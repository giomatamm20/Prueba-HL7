<?php

namespace Config;

use CodeIgniter\Config\BaseConfig;

class Validation extends BaseConfig
{
    public array $ruleSets = [
        \CodeIgniter\Validation\StrictRules\CreditCardRules::class,
        \CodeIgniter\Validation\StrictRules\FileRules::class,
        \CodeIgniter\Validation\StrictRules\FormatRules::class,
        \CodeIgniter\Validation\StrictRules\Rules::class,
    ];
    public array $templates = [];
}
