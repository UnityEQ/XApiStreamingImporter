package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class ReferencedTweet {

    public static final String TYPE_REPLY = "replied_to";
    public static final String TYPE_RETWEET = "retweeted";
    public static final String TYPE_QUOTE = "quoted";

    @JsonProperty("type")
    public String type;

    @JsonProperty("id")
    public String id;
}
