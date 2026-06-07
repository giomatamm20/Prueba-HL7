<?php

namespace Config;

use CodeIgniter\Config\BaseConfig;

class UserAgents extends BaseConfig
{
    public array $platforms = [
        'windows' => 'Windows',
        'linux' => 'Linux',
        'mac' => 'Mac OS',
    ];

    public array $browsers = [
        'Chrome' => 'Chrome',
        'Firefox' => 'Firefox',
        'Safari' => 'Safari',
        'curl' => 'curl',
    ];

    public array $mobiles = [
        'android' => 'Android',
        'iphone' => 'iPhone',
        'ipad' => 'iPad',
    ];

    public array $robots = [
        'bot' => 'Bot',
        'crawl' => 'Crawler',
        'spider' => 'Spider',
    ];
}
